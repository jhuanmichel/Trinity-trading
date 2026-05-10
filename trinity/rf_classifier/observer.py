"""
Observer - registra prob_win SEM filtrar.

Use durante 1-2 semanas apos primeiro treino, ANTES de ligar filtro.
Permite calibrar threshold real sem perder volume.
"""

from __future__ import annotations
import logging
import os
from datetime import datetime, timezone

from trinity.rf_classifier.inference import predict_prob_win
from trinity.rf_classifier.persistence import log_observation

logger = logging.getLogger("rf_classifier.observer")


def is_observation_mode_enabled() -> bool:
    """Modo observacao esta ON?"""
    val = os.getenv("RF_OBSERVATION_MODE", "false")
    return val.lower() in ("true", "1", "yes", "on")


def observe_signal(
    source: str,
    symbol: str,
    direction: str,
    score_composto: float,
    outcome_features: dict,
    filter_threshold: float = 0.55,
) -> None:
    """
    Registra prob_win do RF mas NAO filtra.
    Caller continua enviando sinal normalmente.

    Use pra calibrar threshold real antes de ligar filtro.
    """
    if not is_observation_mode_enabled():
        return

    try:
        prob_win = predict_prob_win(source, outcome_features)

        observation = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "source": source,
            "direction": direction,
            "score_composto": float(score_composto),
            "rf_prob_win": prob_win,
            "filter_threshold": filter_threshold,
            "would_filter": (prob_win is not None and prob_win < filter_threshold),
            "actually_sent": True,  # sempre True em modo observacao
            "outcome": None,
        }

        log_observation(observation)

        if prob_win is not None:
            logger.debug(
                f"[OBS] {symbol} {direction} {source}: "
                f"score={score_composto:.0f} prob_win={prob_win:.3f} "
                f"would_filter={observation['would_filter']}"
            )

    except Exception as e:
        # Fail-silent: observation nao pode quebrar trader
        logger.debug(f"[OBS] erro: {e}")
