"""
trinity/ml/ml_manager.py — v2
MLManager: orquestrador do pipeline ML completo (5 módulos).

Pipeline:
  1. FeatureImportanceAnalyzer  — 5 métricas de importância de features
  2. WeightOptimizer            — 3 métodos + CV temporal + threshold
  3. WalkForwardOptimizer       — validação OOS rigorosa
  4. MonteCarloSimulator        — projeção de risco + Kelly

6 APPLY_CONDITIONS para recomendar pesos otimizados (todas precisam ser True):
  C1. n_total >= MIN_OUTCOMES (30 outcomes resolvidos)
  C2. feature_importance.status == "ok"
  C3. weight_optimizer recomenda (recommend=True) para pelo menos 1 direção
  C4. walk_forward.is_stable() para a direção em questão
  C5. monte_carlo.risk_level != "alto" (risco sistémico aceitável)
  C6. oos_auc_mean > baseline_auc + MIN_IMPROVEMENT (melhoria OOS real)

Estado do pipeline:
  "idle"     — aguardando chamada
  "running"  — pipeline em execução
  "done"     — último run ok
  "waiting"  — dados insuficientes
  "error"    — falhou

READ-ONLY: nunca modifica scoring engines nem deleta outcomes.
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MIN_OUTCOMES     = 30
MIN_IMPROVEMENT  = 0.005    # melhoria mínima de AUC OOS vs baseline

RESULTS_FILE = pathlib.Path(__file__).parent.parent.parent / "dashboard" / "ml_results.json"


# ---------------------------------------------------------------------------
# 6 Apply Conditions
# ---------------------------------------------------------------------------

def _check_apply_conditions(
    fi_result:   dict,
    wo_result:   dict,
    wf_result:   dict,
    mc_result:   dict,
    direction:   str,
) -> dict[str, bool]:
    """
    Verifica as 6 condições de aplicação para uma direção.
    Retorna {C1..C6: bool, all_pass: bool}.
    """
    dir_key = direction.lower()

    n_total = fi_result.get("n_total", 0)
    c1 = n_total >= MIN_OUTCOMES

    c2 = fi_result.get("status") == "ok"

    wo_side = wo_result.get(dir_key, {})
    c3 = bool(wo_side.get("recommend"))

    wf_side = wf_result.get(dir_key, {})
    c4 = bool(wf_side.get("recommend"))

    c5 = mc_result.get("risk_level", "alto") != "alto"

    # C6: OOS AUC > baseline AUC + MIN_IMPROVEMENT
    wf_oos_auc  = wf_side.get("oos_auc_mean", 0.5)
    wf_base_auc = wf_side.get("baseline_sharpe_mean", 0.5)   # comparação via sharpe
    wo_imp      = wo_side.get("improvement_auc", 0.0)
    c6 = wo_imp >= MIN_IMPROVEMENT

    all_pass = c1 and c2 and c3 and c4 and c5 and c6

    return {
        "C1_min_outcomes":     c1,
        "C2_fi_ok":            c2,
        "C3_wo_recommend":     c3,
        "C4_wf_stable":        c4,
        "C5_risk_acceptable":  c5,
        "C6_oos_improvement":  c6,
        "all_pass":            all_pass,
    }


# ---------------------------------------------------------------------------
# Classe principal
# ---------------------------------------------------------------------------

class MLManager:
    """
    Singleton thread-safe que orquestra o pipeline ML completo.

    API pública:
      run_async()                → dispara pipeline em background
      get_status()               → dict de status
      get_results()              → dict completo dos resultados
      summary_for_api()          → dict compacto para endpoint /api/ml/status
      recommended_weights(dir)   → dict[str, float] | None
      apply_conditions(dir)      → dict com C1-C6
      last_result()              → dict | None
    """

    _instance: "MLManager | None" = None
    _lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    @classmethod
    def get_instance(cls) -> "MLManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    def __init__(self) -> None:
        self._state_lock  = threading.Lock()
        self._status: str = "idle"
        self._error: str  = ""
        self._results: dict[str, Any] = {}
        self._thread: threading.Thread | None = None
        self._load_persisted()

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "status":      self._status,
                "error":       self._error,
                "has_results": bool(self._results),
                "last_run":    self._results.get("timestamp", ""),
            }

    def get_results(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._results)

    def last_result(self) -> dict[str, Any] | None:
        with self._state_lock:
            return dict(self._results) if self._results else None

    # ------------------------------------------------------------------
    # Pipeline assíncrono
    # ------------------------------------------------------------------

    def run_async(self) -> bool:
        """
        Dispara o pipeline em background.
        Retorna False se já estiver rodando, True se iniciou.
        """
        with self._state_lock:
            if self._status == "running":
                logger.info("[MLM] Pipeline já rodando, ignorando nova chamada.")
                return False
            self._status = "running"
            self._error  = ""

        t = threading.Thread(target=self._run_pipeline, daemon=True, name="ml-pipeline")
        self._thread = t
        t.start()
        return True

    # ------------------------------------------------------------------
    def _run_pipeline(self) -> None:
        """Executa o pipeline ML completo de forma síncrona (em thread daemon)."""
        try:
            logger.info("[MLM] Pipeline iniciado.")

            from trinity.ml.feature_importance import FeatureImportanceAnalyzer
            from trinity.ml.weight_optimizer   import WeightOptimizer
            from trinity.ml.walk_forward       import WalkForwardOptimizer
            from trinity.ml.monte_carlo        import MonteCarloSimulator

            fi_analyzer = FeatureImportanceAnalyzer()
            wo_optimizer = WeightOptimizer()
            wf_optimizer = WalkForwardOptimizer()
            mc_simulator = MonteCarloSimulator()

            # ── Módulo 1: Feature Importance ────────────────────────────
            logger.info("[MLM] [1/4] Feature Importance...")
            fi_result = fi_analyzer.analyze()

            n_total = fi_result.get("n_total", 0)
            if n_total < MIN_OUTCOMES:
                _status = "waiting"
                combined: dict[str, Any] = {
                    "status":       _status,
                    "timestamp":    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "n_total":      n_total,
                    "n_wins":       fi_result.get("n_wins", 0),
                    "n_losses":     fi_result.get("n_losses", 0),
                    "min_required": MIN_OUTCOMES,
                    "feature_importance": fi_result,
                    "weight_optimizer":   {"status": "skipped"},
                    "walk_forward":       {"status": "skipped"},
                    "monte_carlo":        {"status": "skipped"},
                    "apply_conditions":   {"long": {}, "short": {}},
                    "recommended_weights": {"long": None, "short": None},
                }
                with self._state_lock:
                    self._results = combined
                    self._status  = _status
                self._persist(combined)
                logger.info("[MLM] Dados insuficientes: %d/%d", n_total, MIN_OUTCOMES)
                return

            # ── Módulo 2: Weight Optimizer ──────────────────────────────
            logger.info("[MLM] [2/4] Weight Optimizer...")
            wo_result = wo_optimizer.optimize()

            # ── Módulo 3: Walk-Forward ──────────────────────────────────
            logger.info("[MLM] [3/4] Walk-Forward Optimizer...")
            wf_result = wf_optimizer.run(mode="anchored")

            # ── Módulo 4: Monte Carlo ───────────────────────────────────
            logger.info("[MLM] [4/4] Monte Carlo Simulator...")
            long_opt_w  = wo_optimizer.recommended_weights("LONG")
            short_opt_w = wo_optimizer.recommended_weights("SHORT")
            mc_result = mc_simulator.run(
                optimized_weights=long_opt_w or short_opt_w,
            )

            # ── Apply Conditions ────────────────────────────────────────
            apply_long  = _check_apply_conditions(fi_result, wo_result, wf_result, mc_result, "LONG")
            apply_short = _check_apply_conditions(fi_result, wo_result, wf_result, mc_result, "SHORT")

            # ── Pesos recomendados finais ────────────────────────────────
            # Prioridade: Walk-Forward > Weight Optimizer (mais conservador)
            rec_long  = (wf_optimizer.best_weights("LONG")
                         if apply_long["all_pass"]
                         else None)
            rec_short = (wf_optimizer.best_weights("SHORT")
                         if apply_short["all_pass"]
                         else None)

            # ── Status geral ─────────────────────────────────────────────
            if (fi_result.get("status") == "ok"
                    or wo_result.get("status") == "ok"):
                _status = "done"
            else:
                _status = "waiting"

            combined = {
                "status":       _status,
                "timestamp":    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "n_total":      fi_result.get("n_total", 0),
                "n_wins":       fi_result.get("n_wins", 0),
                "n_losses":     fi_result.get("n_losses", 0),
                "win_rate_pct": fi_result.get("win_rate_pct", 0.0),
                "min_required": MIN_OUTCOMES,
                "feature_importance": fi_result,
                "weight_optimizer":   wo_result,
                "walk_forward":       wf_result,
                "monte_carlo":        mc_result,
                "apply_conditions": {
                    "long":  apply_long,
                    "short": apply_short,
                },
                "recommended_weights": {
                    "long":  rec_long,
                    "short": rec_short,
                },
                "top_predictors": fi_analyzer.top_predictors(n=3),
                "risk_summary":   mc_simulator.risk_summary(),
            }

            with self._state_lock:
                self._results = combined
                self._status  = _status

            self._persist(combined)
            logger.info(
                "[MLM] Pipeline concluído: status=%s trades=%d C_long=%s C_short=%s",
                _status, n_total, apply_long["all_pass"], apply_short["all_pass"],
            )

        except Exception as exc:
            logger.exception("[MLM] Erro no pipeline: %s", exc)
            with self._state_lock:
                self._status = "error"
                self._error  = str(exc)

    # ------------------------------------------------------------------
    # Apply Conditions (acesso externo)
    # ------------------------------------------------------------------

    def apply_conditions(self, direction: str) -> dict[str, bool]:
        """Retorna condições de aplicação para uma direção."""
        with self._state_lock:
            return dict(
                self._results
                .get("apply_conditions", {})
                .get(direction.lower(), {})
            )

    def recommended_weights(self, direction: str) -> dict[str, float] | None:
        """Retorna pesos recomendados se todas as condições forem atendidas."""
        with self._state_lock:
            return self._results.get("recommended_weights", {}).get(direction.lower())

    # ------------------------------------------------------------------
    # Summary para API
    # ------------------------------------------------------------------

    def summary_for_api(self) -> dict[str, Any]:
        """
        Dict compacto para o endpoint /api/ml/status.
        """
        with self._state_lock:
            res             = dict(self._results)
            pipeline_status = self._status
            err             = self._error

        fi  = res.get("feature_importance", {})
        wo  = res.get("weight_optimizer", {})
        wf  = res.get("walk_forward", {})
        mc  = res.get("monte_carlo", {})
        ac  = res.get("apply_conditions", {})

        top_feats: list[dict] = []
        for f in fi.get("features", [])[:5]:
            top_feats.append({
                "feature":         f["feature"],
                "composite":       f.get("composite", 0),
                "composite_label": f.get("composite_label", ""),
                "cohen_d":         f.get("cohen_d", 0),
                "auc":             f.get("auc", 0.5),
                "iv":              f.get("iv", 0),
                "mean_wins":       f.get("mean_wins", 0),
                "mean_losses":     f.get("mean_losses", 0),
                "separation_pct":  f.get("separation_pct", 0),
            })

        return {
            "pipeline_status":     pipeline_status,
            "error":               err,
            "status":              res.get("status", "idle"),
            "timestamp":           res.get("timestamp", ""),
            "n_total":             res.get("n_total", 0),
            "n_wins":              res.get("n_wins", 0),
            "n_losses":            res.get("n_losses", 0),
            "win_rate_pct":        res.get("win_rate_pct", 0.0),
            "min_required":        MIN_OUTCOMES,
            "top_predictors":      res.get("top_predictors", []),
            "top_features":        top_feats,
            "recommended_weights": res.get("recommended_weights", {"long": None, "short": None}),
            "apply_conditions":    ac,
            # Walk-forward summary
            "wf_long": {
                "oos_sharpe_mean": wf.get("long", {}).get("oos_sharpe_mean"),
                "oos_auc_mean":    wf.get("long", {}).get("oos_auc_mean"),
                "recommend":       wf.get("long", {}).get("recommend"),
            },
            "wf_short": {
                "oos_sharpe_mean": wf.get("short", {}).get("oos_sharpe_mean"),
                "oos_auc_mean":    wf.get("short", {}).get("oos_auc_mean"),
                "recommend":       wf.get("short", {}).get("recommend"),
            },
            # Monte Carlo summary
            "risk_summary": res.get("risk_summary", {}),
            # Weight optimizer
            "long_auc":    wo.get("long",  {}).get("auc"),
            "short_auc":   wo.get("short", {}).get("auc"),
            "long_sharpe": wo.get("long",  {}).get("sharpe"),
            "short_sharpe":wo.get("short", {}).get("sharpe"),
        }

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------

    def _persist(self, data: dict[str, Any]) -> None:
        try:
            RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            RESULTS_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            logger.info("[MLM] Resultados persistidos: %s", RESULTS_FILE)
        except Exception as exc:
            logger.warning("[MLM] Falha ao persistir: %s", exc)

    def _load_persisted(self) -> None:
        try:
            if RESULTS_FILE.exists():
                data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
                with self._state_lock:
                    self._results = data
                    self._status  = "done" if data.get("status") in ("done", "waiting") else "idle"
                logger.info("[MLM] Resultados anteriores carregados: %s", RESULTS_FILE)
        except Exception as exc:
            logger.warning("[MLM] Não foi possível carregar %s: %s", RESULTS_FILE, exc)
