"""
trinity/ml/weight_optimizer.py
Otimiza os pesos dos detectors via random search + hill climbing,
maximizando a separação WIN/LOSS (pseudo-AUC via Mann-Whitney U).

Estratégia:
  1. Random search (N_RANDOM=5000 iterações): amostra pesos aleatórios,
     avalia separação com o conjunto de outcomes.
  2. Hill climbing (N_HILL=1000): parte do melhor random, perturba um peso
     por vez e aceita se melhorar.

Saída: dict com pesos recomendados por direção (LONG / SHORT).
Pipeline é READ-ONLY: nunca modifica os scoring engines.
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

MIN_OUTCOMES = 30
N_RANDOM     = 5_000
N_HILL       = 1_000
PERTURBATION = 0.05   # ±5 pts por step de hill climbing

# Detectors por direção
DETECTORS_LONG  = ["silent_acc", "squeeze", "gravity", "breakout"]
DETECTORS_SHORT = ["cascade",   "collapse", "whale",   "volatility"]
TOTAL_PTS       = 100.0   # soma dos pesos deve ser 100

LOGS_DIR = pathlib.Path(__file__).parent.parent.parent / "logs"


# ---------------------------------------------------------------------------
# Carregamento (replicado aqui para não depender de feature_importance)
# ---------------------------------------------------------------------------

def _load_resolved_by_direction() -> dict[str, list[dict]]:
    """Retorna {"LONG": [...], "SHORT": [...]} de outcomes resolvidos."""
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
        except Exception as e:
            logger.warning("[ML] Erro ao ler %s: %s", fpath.name, e)

    return data


# ---------------------------------------------------------------------------
# Avaliação de pesos (Mann-Whitney U simplificado — separação de scores)
# ---------------------------------------------------------------------------

def _score_with_weights(outcome: dict, weights: dict[str, float]) -> float:
    """Recalcula o score composto de um outcome usando pesos dados."""
    ls = outcome.get("layer_scores") or {}
    total = 0.0
    for feat, w in weights.items():
        raw = ls.get(feat, 0.0)
        total += float(raw) * w
    return total


def _mann_whitney_u_statistic(wins_scores: list[float], losses_scores: list[float]) -> float:
    """
    Calcula AUC via Mann-Whitney U: probabilidade de que um WIN
    tenha score maior que um LOSS.
    P(WIN_score > LOSS_score) — queremos maximizar isso (ideal = 1.0).
    """
    if not wins_scores or not losses_scores:
        return 0.5

    n_wins   = len(wins_scores)
    n_losses = len(losses_scores)
    u = 0
    for ws in wins_scores:
        for ls in losses_scores:
            if ws > ls:
                u += 1
            elif ws == ls:
                u += 0.5

    return u / (n_wins * n_losses)


def _evaluate_weights(outcomes: list[dict], weights: dict[str, float]) -> float:
    """
    Avalia um conjunto de pesos: retorna AUC (Mann-Whitney U).
    Scores recalculados; retorna 0.5 se dados insuficientes.
    """
    wins   = [o for o in outcomes if o["status"] == "WIN"]
    losses = [o for o in outcomes if o["status"] == "LOSS"]

    if not wins or not losses:
        return 0.5

    wins_scores   = [_score_with_weights(o, weights) for o in wins]
    losses_scores = [_score_with_weights(o, weights) for o in losses]

    return _mann_whitney_u_statistic(wins_scores, losses_scores)


# ---------------------------------------------------------------------------
# Geração de pesos aleatórios
# ---------------------------------------------------------------------------

def _random_weights(detectors: list[str], rng: random.Random) -> dict[str, float]:
    """Gera pesos aleatórios normalizados para TOTAL_PTS."""
    raw = [rng.uniform(1, 50) for _ in detectors]
    total = sum(raw)
    return {d: raw[i] / total * TOTAL_PTS for i, d in enumerate(detectors)}


def _perturb_weights(weights: dict[str, float], detectors: list[str], rng: random.Random) -> dict[str, float]:
    """Perturba um peso aleatório ±PERTURBATION e renormaliza."""
    w = dict(weights)
    feat = rng.choice(detectors)
    delta = rng.uniform(-PERTURBATION * TOTAL_PTS, PERTURBATION * TOTAL_PTS)
    w[feat] = max(1.0, w[feat] + delta)

    # Renormaliza para TOTAL_PTS
    total = sum(w.values())
    return {k: v / total * TOTAL_PTS for k, v in w.items()}


# ---------------------------------------------------------------------------
# Otimização por direção
# ---------------------------------------------------------------------------

def _optimize_for_direction(
    outcomes: list[dict],
    detectors: list[str],
    seed: int = 42,
) -> dict[str, Any]:
    """
    Roda random search + hill climbing para um conjunto de outcomes (LONG ou SHORT).
    Retorna: {weights, auc, n_total, n_wins, n_losses, improvement_pct}
    """
    wins   = [o for o in outcomes if o.get("status") == "WIN"]
    losses = [o for o in outcomes if o.get("status") == "LOSS"]
    n_total = len(wins) + len(losses)

    result: dict[str, Any] = {
        "n_total":  n_total,
        "n_wins":   len(wins),
        "n_losses": len(losses),
    }

    if n_total < MIN_OUTCOMES:
        result["status"] = "insufficient_data"
        result["weights"] = {d: TOTAL_PTS / len(detectors) for d in detectors}
        result["auc"] = 0.5
        result["improvement_pct"] = 0.0
        return result

    rng = random.Random(seed)

    # AUC dos pesos uniformes (baseline)
    uniform_weights = {d: TOTAL_PTS / len(detectors) for d in detectors}
    baseline_auc    = _evaluate_weights(outcomes, uniform_weights)

    # --- Fase 1: Random search ---
    best_weights = uniform_weights
    best_auc     = baseline_auc

    for _ in range(N_RANDOM):
        w   = _random_weights(detectors, rng)
        auc = _evaluate_weights(outcomes, w)
        if auc > best_auc:
            best_auc     = auc
            best_weights = w

    # --- Fase 2: Hill climbing ---
    current_weights = dict(best_weights)
    current_auc     = best_auc

    for _ in range(N_HILL):
        candidate = _perturb_weights(current_weights, detectors, rng)
        auc        = _evaluate_weights(outcomes, candidate)
        if auc > current_auc:
            current_auc     = auc
            current_weights = candidate

    best_weights = current_weights
    best_auc     = current_auc

    improvement = (best_auc - baseline_auc) / baseline_auc * 100 if baseline_auc > 0 else 0.0

    result["status"]          = "ok"
    result["weights"]         = {k: round(v, 2) for k, v in best_weights.items()}
    result["auc"]             = round(best_auc, 4)
    result["baseline_auc"]    = round(baseline_auc, 4)
    result["improvement_pct"] = round(improvement, 2)

    logger.info(
        "[ML] Otimização concluída (%d outcomes): AUC %.4f → %.4f (+%.1f%%)",
        n_total, baseline_auc, best_auc, improvement,
    )
    return result


# ---------------------------------------------------------------------------
# Classe principal
# ---------------------------------------------------------------------------

class WeightOptimizer:
    """
    Otimiza pesos dos detectors por direção (LONG / SHORT).
    READ-ONLY: retorna recomendações, nunca modifica os engines.
    """

    def __init__(self) -> None:
        self._last_result: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    def optimize(self, seed: int = 42) -> dict[str, Any]:
        """
        Roda otimização completa e retorna:
          status, timestamp, long, short (cada um com weights, auc, etc.)
        """
        by_dir = _load_resolved_by_direction()

        long_result  = _optimize_for_direction(by_dir["LONG"],  DETECTORS_LONG,  seed=seed)
        short_result = _optimize_for_direction(by_dir["SHORT"], DETECTORS_SHORT, seed=seed)

        # Status geral: ok se pelo menos um lado tem dados suficientes
        if long_result.get("status") == "ok" or short_result.get("status") == "ok":
            overall_status = "ok"
        else:
            overall_status = "insufficient_data"

        result: dict[str, Any] = {
            "status":    overall_status,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "long":      long_result,
            "short":     short_result,
        }

        self._last_result = result
        return result

    # ------------------------------------------------------------------
    def last_result(self) -> dict[str, Any] | None:
        return self._last_result

    # ------------------------------------------------------------------
    def recommended_weights(self, direction: str) -> dict[str, float] | None:
        """Retorna pesos recomendados para LONG ou SHORT. None se não disponível."""
        if not self._last_result:
            return None
        key = direction.upper()
        side = self._last_result.get(key.lower())
        if not side or side.get("status") != "ok":
            return None
        return side.get("weights")
