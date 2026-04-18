"""
trinity/ml/walk_forward.py
WalkForwardOptimizer: validação OOS rigorosa dos pesos otimizados.

Dois modos de janela:
  - Anchored  (padrão): treino começa sempre no início — janela cresce
  - Rolling:            janela desliza com tamanho fixo TRAIN_SIZE

Para cada janela:
  1. Otimiza pesos no bloco de treino (random_search leve)
  2. Avalia no bloco de teste OOS (Score Sharpe + AUC + Win Rate)
  3. Compara com pesos uniformes no mesmo bloco OOS

Métricas de saída:
  - oos_sharpe_mean / oos_sharpe_std
  - oos_auc_mean   / oos_auc_std
  - degradation:   IS_sharpe − OOS_sharpe  (ideal < 0.5)
  - stability:     std(OOS_sharpe_per_fold) (ideal < 0.3)
  - consistency:   fração de folds onde OOS > baseline

Auto-regularização:
  Se degradation > DEGRADE_THRESH → usa pesos uniformes (conservador)

READ-ONLY: nunca modifica scoring engines.
"""

from __future__ import annotations

import json
import logging
import math
import pathlib
import random
import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MIN_TRAIN   = 40   # mínimo de outcomes no bloco de treino
MIN_TEST    = 15   # mínimo de outcomes no bloco de teste
TEST_SIZE   = 20   # trades OOS por fold (rolling e anchored)
TRAIN_SIZE  = 80   # trades de treino (modo rolling)

N_RANDOM_WF = 2_000   # random search por fold (mais leve que o optimizer full)
N_HILL_WF   = 400

DEGRADE_THRESH   = 0.50   # IS-OOS > 0.5 → overfitting → conservador
STABILITY_THRESH = 0.30   # std(OOS_sharpe) > 0.3 → instável
CONSISTENCY_MIN  = 0.60   # fração mínima de folds OOS > baseline

W_MIN = 5.0
W_MAX = 50.0
W_SUM = 100.0

DETECTORS_LONG  = ["silent_acc", "squeeze", "gravity", "breakout"]
DETECTORS_SHORT = ["cascade",   "collapse", "whale",   "volatility"]

LOGS_DIR     = (pathlib.Path("/data/logs") if pathlib.Path("/data").exists()
                else pathlib.Path(__file__).parent.parent.parent / "logs")
RESULTS_FILE = pathlib.Path(__file__).parent.parent.parent / "dashboard" / "ml_walk_forward.json"


# ---------------------------------------------------------------------------
# Importações internas (sem criar dependências circulares)
# ---------------------------------------------------------------------------

def _import_optimizer():
    from trinity.ml.weight_optimizer import (
        _random_search, _hill_climbing, _score_sharpe,
        _auc_mann_whitney, _uniform,
    )
    return _random_search, _hill_climbing, _score_sharpe, _auc_mann_whitney, _uniform


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _load_by_direction() -> dict[str, list[dict]]:
    """Carrega outcomes resolvidos ordenados por timestamp."""
    data: dict[str, list] = {"LONG": [], "SHORT": []}
    files = sorted(LOGS_DIR.glob("outcomes_*.jsonl"))
    for fpath in files:
        try:
            for line in fpath.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("status") not in ("WIN", "LOSS"):
                    continue
                d = o.get("direction", "")
                if d in data:
                    data[d].append(o)
        except Exception as exc:
            logger.warning("[WF] Erro ao ler %s: %s", fpath.name, exc)

    for key in data:
        data[key].sort(key=lambda o: o.get("resolved_at", o.get("timestamp", "")))

    return data


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------

def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _sharpe_label(sharpe: float) -> str:
    if sharpe >= 1.0:  return "excelente"
    if sharpe >= 0.5:  return "bom"
    if sharpe >= 0.0:  return "neutro"
    return "negativo"


# ---------------------------------------------------------------------------
# Walk-Forward por direção
# ---------------------------------------------------------------------------

def _walk_forward_direction(
    outcomes: list[dict],
    detectors: list[str],
    mode: str = "anchored",
    seed: int = 42,
) -> dict[str, Any]:
    """
    Roda walk-forward para uma direção (LONG ou SHORT).

    mode: "anchored" (treino cresce) | "rolling" (janela fixa)
    """
    _rs, _hc, _sharpe, _auc, _unif = _import_optimizer()

    n = len(outcomes)
    wins_total  = sum(1 for o in outcomes if o.get("status") == "WIN")
    losses_total = n - wins_total

    base_result: dict[str, Any] = {
        "n_total":  n,
        "n_wins":   wins_total,
        "n_losses": losses_total,
        "mode":     mode,
    }

    if n < MIN_TRAIN + MIN_TEST:
        base_result["status"] = "insufficient_data"
        base_result["recommend"] = False
        return base_result

    rng = random.Random(seed)
    folds: list[dict] = []

    # Gerar janelas (treino/teste)
    windows = []
    if mode == "anchored":
        # Treino começa em 0, cresce
        cursor = MIN_TRAIN
        while cursor + MIN_TEST <= n:
            train_end  = cursor
            test_start = cursor
            test_end   = min(cursor + TEST_SIZE, n)
            windows.append((0, train_end, test_start, test_end))
            cursor += TEST_SIZE
    else:  # rolling
        cursor = TRAIN_SIZE
        while cursor + MIN_TEST <= n:
            train_start = max(0, cursor - TRAIN_SIZE)
            train_end   = cursor
            test_start  = cursor
            test_end    = min(cursor + TEST_SIZE, n)
            windows.append((train_start, train_end, test_start, test_end))
            cursor += TEST_SIZE

    if not windows:
        base_result["status"] = "insufficient_data"
        base_result["recommend"] = False
        return base_result

    is_sharpes  = []   # IS = In-Sample
    oos_sharpes = []   # OOS = Out-of-Sample
    oos_aucs    = []
    baseline_sharpes = []

    for i, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
        train = outcomes[tr_s:tr_e]
        test  = outcomes[te_s:te_e]

        tr_wins  = sum(1 for o in train if o.get("status") == "WIN")
        tr_loss  = len(train) - tr_wins
        te_wins  = sum(1 for o in test  if o.get("status") == "WIN")
        te_loss  = len(test)  - te_wins

        if tr_wins < 5 or tr_loss < 5 or te_wins < 2 or te_loss < 2:
            folds.append({"fold": i + 1, "skipped": True, "reason": "poucos WIN ou LOSS"})
            continue

        # Otimizar no treino
        best_w, _ = _rs(train, detectors, n_iter=N_RANDOM_WF, rng=random.Random(seed + i))
        best_w, _ = _hc(train, detectors, best_w, n_iter=N_HILL_WF, rng=random.Random(seed + i + 100))

        unif_w = _unif(detectors)

        # Avaliar no treino (IS)
        is_sharpe = _sharpe(train, best_w)
        is_sharpes.append(is_sharpe)

        # Avaliar no teste (OOS)
        oos_sharpe   = _sharpe(test, best_w)
        oos_auc      = _auc(test, best_w)
        base_sharpe  = _sharpe(test, unif_w)
        base_auc     = _auc(test, unif_w)

        oos_sharpes.append(oos_sharpe)
        oos_aucs.append(oos_auc)
        baseline_sharpes.append(base_sharpe)

        folds.append({
            "fold":        i + 1,
            "train_n":     len(train),
            "test_n":      len(test),
            "train_wins":  tr_wins,
            "test_wins":   te_wins,
            "is_sharpe":   round(is_sharpe, 4),
            "oos_sharpe":  round(oos_sharpe, 4),
            "oos_auc":     round(oos_auc, 4),
            "base_sharpe": round(base_sharpe, 4),
            "base_auc":    round(base_auc, 4),
            "beat_baseline": oos_sharpe > base_sharpe,
            "weights":     {k: round(v, 2) for k, v in best_w.items()},
        })

    valid = [f for f in folds if not f.get("skipped")]
    if not valid:
        base_result["status"] = "insufficient_data"
        base_result["recommend"] = False
        base_result["folds"] = folds
        return base_result

    # ── Métricas agregadas ──────────────────────────────────────────────
    is_mean   = _mean(is_sharpes)
    oos_mean  = _mean(oos_sharpes)
    oos_std   = _std(oos_sharpes)
    oos_auc_m = _mean(oos_aucs)
    base_mean = _mean(baseline_sharpes)

    degradation  = is_mean - oos_mean
    stability    = oos_std
    n_beat       = sum(1 for f in valid if f.get("beat_baseline"))
    consistency  = n_beat / len(valid) if valid else 0.0

    # ── Auto-regularização ──────────────────────────────────────────────
    overfitting   = degradation > DEGRADE_THRESH
    unstable      = stability    > STABILITY_THRESH
    inconsistent  = consistency  < CONSISTENCY_MIN

    # Pesos finais: média dos folds válidos (ensemble de walk-forward)
    final_weights: dict[str, float] = {}
    for feat in detectors:
        vals = [f["weights"].get(feat, W_SUM / len(detectors)) for f in valid]
        final_weights[feat] = round(_mean(vals), 2)
    # Re-normalizar
    total = sum(final_weights.values())
    final_weights = {k: round(v / total * W_SUM, 2) for k, v in final_weights.items()}

    recommend = not overfitting and not unstable and not inconsistent

    base_result.update({
        "status":             "ok",
        "n_folds_run":        len(valid),
        "is_sharpe_mean":     round(is_mean, 4),
        "oos_sharpe_mean":    round(oos_mean, 4),
        "oos_sharpe_std":     round(oos_std, 4),
        "oos_sharpe_label":   _sharpe_label(oos_mean),
        "oos_auc_mean":       round(oos_auc_m, 4),
        "baseline_sharpe_mean": round(base_mean, 4),
        "degradation":        round(degradation, 4),
        "stability":          round(stability, 4),
        "consistency":        round(consistency, 4),
        "overfitting":        overfitting,
        "unstable":           unstable,
        "inconsistent":       inconsistent,
        "recommend":          recommend,
        "weights":            final_weights,
        "folds":              folds,
    })

    logger.info(
        "[WF] (%s, %d outcomes): OOS_sharpe=%.3f±%.3f deg=%.3f cons=%.0f%% recommend=%s",
        mode, n, oos_mean, oos_std, degradation, consistency * 100, recommend,
    )
    return base_result


# ---------------------------------------------------------------------------
# Classe pública
# ---------------------------------------------------------------------------

class WalkForwardOptimizer:
    """
    Validação OOS de pesos otimizados via walk-forward.
    READ-ONLY: nunca modifica scoring engines.

    API pública:
      run(mode="anchored", seed=42) → dict completo
      is_stable(direction) → bool
      best_weights(direction) → dict[str, float] | None
      export_json(path=None) → persiste resultado
      last_result() → dict | None
    """

    def __init__(self) -> None:
        self._last_result: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    def run(self, mode: str = "anchored", seed: int = 42) -> dict[str, Any]:
        """
        Roda walk-forward para LONG e SHORT.
        mode: "anchored" | "rolling"
        """
        by_dir = _load_by_direction()

        long_r  = _walk_forward_direction(by_dir["LONG"],  DETECTORS_LONG,  mode=mode, seed=seed)
        short_r = _walk_forward_direction(by_dir["SHORT"], DETECTORS_SHORT, mode=mode, seed=seed + 1)

        overall = "ok" if (
            long_r.get("status") == "ok" or short_r.get("status") == "ok"
        ) else "insufficient_data"

        result: dict[str, Any] = {
            "status":    overall,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "mode":      mode,
            "long":      long_r,
            "short":     short_r,
        }
        self._last_result = result
        return result

    # ------------------------------------------------------------------
    def is_stable(self, direction: str) -> bool:
        """Retorna True se o walk-forward indicar pesos estáveis OOS."""
        if not self._last_result:
            return False
        side = self._last_result.get(direction.lower())
        return bool(side and side.get("recommend"))

    # ------------------------------------------------------------------
    def best_weights(self, direction: str) -> dict[str, float] | None:
        """Retorna pesos walk-forward se recomendados, None caso contrário."""
        if not self._last_result:
            return None
        side = self._last_result.get(direction.lower())
        if not side or not side.get("recommend"):
            return None
        return side.get("weights")

    # ------------------------------------------------------------------
    def last_result(self) -> dict[str, Any] | None:
        return self._last_result

    # ------------------------------------------------------------------
    def export_json(self, path: pathlib.Path | None = None) -> pathlib.Path:
        """Persiste último resultado em JSON."""
        if not self._last_result:
            raise RuntimeError("[WF] Nada para exportar — chame run() primeiro")
        out = path or RESULTS_FILE
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self._last_result, indent=2, default=str), encoding="utf-8")
        logger.info("[WF] Resultado exportado: %s", out)
        return out
