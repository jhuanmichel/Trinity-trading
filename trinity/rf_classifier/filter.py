"""
Filter - aplica filtro RF em producao (com kill switch).

Decide se sinal passa ou bloqueia baseado em prob_win.

Fail-open: erro = passa o sinal.
Modo observacao tem precedencia: se RF_OBSERVATION_MODE=true, NAO filtra.
"""

from __future__ import annotations
import logging
import os
from typing import Optional

from trinity.rf_classifier.inference import predict_prob_win
from trinity.rf_classifier.observer import is_observation_mode_enabled
from trinity.rf_classifier.persistence import log_observation

logger = logging.getLogger("rf_classifier.filter")


def is_filter_enabled() -> bool:
    """Filter habilitado?"""
    master = os.getenv("RF_CLASSIFIER_ENABLED", "false")
    if master.lower() not in ("true", "1", "yes", "on"):
        return False

    # Em modo observacao, filter retorna False (nao filtra)
    if is_observation_mode_enabled():
        return False

    return True


def get_filter_threshold() -> float:
    """Threshold de filtro (env var ou default)."""
    val = os.getenv("RF_FILTER_THRESHOLD", "0.55")
    try:
        return float(val)
    except ValueError:
        return 0.55


def should_filter(
    source: str,
    symbol: str,
    direction: str,
    score_composto: float,
    outcome_features: dict,
) -> tuple[bool, str, Optional[float]]:
    """
    Decide se sinal deve ser FILTRADO (bloqueado).

    Returns:
        (should_filter, reason, prob_win)
        should_filter=True -> bloquear sinal
        should_filter=False -> deixar passar

    Fail-open: erro = (False, "fail_open", None).
    """
    if not is_filter_enabled():
        return (False, "filter_disabled", None)

    try:
        prob_win = predict_prob_win(source, outcome_features)

        if prob_win is None:
            return (False, "no_model_for_source", None)

        threshold = get_filter_threshold()

        if prob_win < threshold:
            reason = f"rf_below_threshold:{prob_win:.3f}<{threshold:.2f}"

            try:
                from datetime import datetime, timezone
                log_observation({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol,
                    "source": source,
                    "direction": direction,
                    "score_composto": float(score_composto),
                    "rf_prob_win": prob_win,
                    "filter_threshold": threshold,
                    "would_filter": True,
                    "actually_sent": False,
                    "outcome": None,
                })
            except Exception:
                pass

            return (True, reason, prob_win)

        return (False, f"rf_above_threshold:{prob_win:.3f}>={threshold:.2f}", prob_win)

    except Exception as e:
        logger.debug(f"[FILTER] erro: {e}")
        return (False, f"fail_open:{type(e).__name__}", None)
