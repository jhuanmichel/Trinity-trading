"""
Helper para traders chamarem o RF de forma simples.
"""

from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger("rf_classifier.trader_helper")


def evaluate_rf(
    source: str,
    symbol: str,
    direction: str,
    score_composto: float,
    outcome_features: dict,
) -> dict:
    """
    Avalia sinal com RF.

    Returns:
        {
            "prob_win": float | None,
            "should_filter": bool,
            "reason": str,
            "mode": "observation" | "filter" | "disabled",
        }

    Caller pode usar `should_filter` direto:
        if result["should_filter"]:
            return  # bloquear sinal
    """
    try:
        from trinity.rf_classifier.observer import (
            is_observation_mode_enabled,
            observe_signal,
        )
        from trinity.rf_classifier.filter import should_filter as _should_filter

        # Modo observacao: registra mas nao filtra
        if is_observation_mode_enabled():
            observe_signal(
                source=source,
                symbol=symbol,
                direction=direction,
                score_composto=score_composto,
                outcome_features=outcome_features,
            )
            return {
                "prob_win": None,
                "should_filter": False,
                "reason": "observation_mode",
                "mode": "observation",
            }

        # Modo filtro
        block, reason, prob_win = _should_filter(
            source=source,
            symbol=symbol,
            direction=direction,
            score_composto=score_composto,
            outcome_features=outcome_features,
        )

        return {
            "prob_win": prob_win,
            "should_filter": block,
            "reason": reason,
            "mode": "filter" if block or prob_win is not None else "disabled",
        }

    except Exception as e:
        # Fail-open
        logger.debug(f"[RF_HELPER] erro: {e}")
        return {
            "prob_win": None,
            "should_filter": False,
            "reason": f"fail_open:{type(e).__name__}",
            "mode": "error",
        }


def build_outcome_features(
    score: float,
    component_scores: dict,
    direction: str,
    btc_regime: str = "UNKNOWN",
    is_blue_chip: bool = False,
    conviction_tier: str = "UNKNOWN",
) -> dict:
    """
    Constroi dict de features no formato que o feature_extractor espera.

    Usado pelos traders ANTES de enviar Telegram.
    """
    from datetime import datetime, timezone
    return {
        "score":            float(score),
        "component_scores": dict(component_scores or {}),
        "direction":        str(direction).upper(),
        "btc_regime":       str(btc_regime).upper(),
        "is_blue_chip":     bool(is_blue_chip),
        "conviction_tier":  str(conviction_tier).upper(),
        "timestamp":        datetime.now(timezone.utc).isoformat(),
    }
