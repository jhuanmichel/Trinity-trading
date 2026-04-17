"""
trinity/ml/ml_manager.py
Orquestrador do pipeline de ML: Feature Importance + Weight Optimizer.

Responsabilidades:
  - Singleton thread-safe para o servidor FastAPI
  - Executa o pipeline em background (threading.Thread)
  - Expõe status e resultados via get_status() / get_results()
  - Persiste resultados em dashboard/ml_results.json (para sobreviver deploys)
  - READ-ONLY: nunca modifica scoring engines nem deleta outcomes

Estado do pipeline:
  "idle"       — aguardando chamada
  "running"    — pipeline em execução
  "done"       — último run concluído com sucesso
  "error"      — último run falhou
  "waiting"    — dados insuficientes (< MIN_OUTCOMES outcomes resolvidos)
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Arquivo de resultados persistido junto ao dashboard
RESULTS_FILE = pathlib.Path(__file__).parent.parent.parent / "dashboard" / "ml_results.json"

MIN_OUTCOMES = 30


class MLManager:
    """Singleton que orquestra Feature Importance + Weight Optimizer."""

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
        self._state_lock   = threading.Lock()
        self._status: str  = "idle"
        self._error: str   = ""
        self._results: dict[str, Any] = {}
        self._thread: threading.Thread | None = None
        self._load_persisted()

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "status":     self._status,
                "error":      self._error,
                "has_results": bool(self._results),
                "last_run":   self._results.get("timestamp", ""),
            }

    def get_results(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._results)

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def run_async(self) -> bool:
        """
        Dispara o pipeline em background.
        Retorna False se já estiver rodando, True se iniciou.
        """
        with self._state_lock:
            if self._status == "running":
                logger.info("[ML] Pipeline já está rodando, ignorando nova chamada.")
                return False
            self._status = "running"
            self._error  = ""

        t = threading.Thread(target=self._run_pipeline, daemon=True, name="ml-pipeline")
        self._thread = t
        t.start()
        return True

    # ------------------------------------------------------------------
    def _run_pipeline(self) -> None:
        """Executa Feature Importance + Weight Optimizer de forma síncrona."""
        try:
            logger.info("[ML] Pipeline iniciado.")
            from trinity.ml.feature_importance import FeatureImportanceAnalyzer
            from trinity.ml.weight_optimizer   import WeightOptimizer

            fi  = FeatureImportanceAnalyzer()
            wo  = WeightOptimizer()

            fi_result = fi.analyze()
            wo_result = wo.optimize()

            n_total = fi_result.get("n_total", 0)
            if n_total < MIN_OUTCOMES:
                status = "waiting"
            elif fi_result.get("status") == "ok" or wo_result.get("status") == "ok":
                status = "done"
            else:
                status = "waiting"

            combined: dict[str, Any] = {
                "status":             status,
                "timestamp":          datetime.datetime.utcnow().isoformat() + "Z",
                "n_total":            fi_result.get("n_total", 0),
                "n_wins":             fi_result.get("n_wins", 0),
                "n_losses":           fi_result.get("n_losses", 0),
                "min_required":       MIN_OUTCOMES,
                "feature_importance": fi_result,
                "weight_optimizer":   wo_result,
                "top_predictors":     fi.top_predictors(n=3),
                "recommended_weights": {
                    "long":  wo.recommended_weights("LONG"),
                    "short": wo.recommended_weights("SHORT"),
                },
            }

            with self._state_lock:
                self._results = combined
                self._status  = status

            self._persist(combined)
            logger.info("[ML] Pipeline concluído. status=%s, trades=%d", status, n_total)

        except Exception as e:
            logger.exception("[ML] Erro no pipeline: %s", e)
            with self._state_lock:
                self._status = "error"
                self._error  = str(e)

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------

    def _persist(self, data: dict[str, Any]) -> None:
        """Salva resultados em dashboard/ml_results.json."""
        try:
            RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            RESULTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            logger.info("[ML] Resultados persistidos em %s", RESULTS_FILE)
        except Exception as e:
            logger.warning("[ML] Falha ao persistir: %s", e)

    def _load_persisted(self) -> None:
        """Carrega resultados salvos anteriormente (se existirem)."""
        try:
            if RESULTS_FILE.exists():
                data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
                with self._state_lock:
                    self._results = data
                    self._status  = "done" if data.get("status") in ("done", "waiting") else "idle"
                logger.info("[ML] Resultados anteriores carregados de %s", RESULTS_FILE)
        except Exception as e:
            logger.warning("[ML] Não foi possível carregar %s: %s", RESULTS_FILE, e)

    # ------------------------------------------------------------------
    # Helpers para o dashboard
    # ------------------------------------------------------------------

    def summary_for_api(self) -> dict[str, Any]:
        """
        Retorna dict compacto para o endpoint /api/ml/status.
        Inclui apenas o necessário para o dashboard.
        """
        with self._state_lock:
            res = dict(self._results)
            pipeline_status = self._status
            err = self._error

        fi = res.get("feature_importance", {})
        wo = res.get("weight_optimizer", {})

        top_feats = []
        for f in fi.get("features", [])[:5]:
            top_feats.append({
                "feature":        f["feature"],
                "cohen_d":        f["cohen_d"],
                "separation_pct": f["separation_pct"],
                "mean_wins":      f["mean_wins"],
                "mean_losses":    f["mean_losses"],
            })

        return {
            "pipeline_status":   pipeline_status,
            "error":             err,
            "status":            res.get("status", "idle"),
            "timestamp":         res.get("timestamp", ""),
            "n_total":           res.get("n_total", 0),
            "n_wins":            res.get("n_wins",  0),
            "n_losses":          res.get("n_losses", 0),
            "min_required":      MIN_OUTCOMES,
            "top_predictors":    res.get("top_predictors", []),
            "top_features":      top_feats,
            "recommended_weights": res.get("recommended_weights", {}),
            "long_auc":          wo.get("long",  {}).get("auc", None),
            "short_auc":         wo.get("short", {}).get("auc", None),
        }
