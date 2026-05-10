"""
Helpers pros traders consultarem o auto-learning state.
Todos os metodos sao FAIL-OPEN: erros nao bloqueiam sinais.
"""

from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger("auto_learning.trader_integration")


def get_active_threshold(
    signal_type: str = "pump",
    is_blue_chip: bool = False,
    fallback: float = 80.0,
) -> float:
    """
    Threshold ativo do auto-learning.
    Fail-open: retorna fallback em qualquer erro.
    """
    try:
        from trinity.auto_learning import state
        return state.get_threshold(signal_type, is_blue_chip=is_blue_chip)
    except Exception as e:
        logger.debug(f"[TRADER_INT] threshold fallback {fallback}: {e}")
        return fallback


def passes_auto_learning_filters(
    symbol: str,
    score: float,
    direction: str,
    btc_regime: str,
) -> tuple[bool, str]:
    """
    Aplica filtros (whitelist > blacklist > regime gate).

    Returns:
        (allow, reason)

    Fail-open: erro retorna (True, "fail_open:<motivo>").
    """
    try:
        from trinity.auto_learning import state, performance_guard

        # Performance guard ativo? Continua passando sinais (fail-open).
        if performance_guard.is_killed_by_guard():
            return (True, "guard_killed_pass_through")

        sym = (symbol or "").upper()

        # Whitelist tem prioridade
        if state.is_symbol_whitelisted(sym):
            return (True, "whitelisted")

        # Blacklist
        if state.is_symbol_blacklisted(sym):
            return (False, "blacklisted")

        # Regime gate
        rule = state.get_regime_gate_rule(direction, btc_regime)
        if rule == "BLOCK":
            return (False, f"regime_block_{direction}_{btc_regime}")
        if rule == "RESTRICT_SCORE":
            if not (40 <= score <= 55):
                return (False, f"regime_restrict_score_{score:.0f}")

        return (True, "pass")

    except Exception as e:
        logger.debug(f"[TRADER_INT] filters fail_open: {e}")
        return (True, f"fail_open:{type(e).__name__}")


def normalize_btc_regime(regime: Any) -> str:
    """
    Normaliza retorno de get_btc_regime() pra string upper.
    Aceita string OU dict {"regime": "X"}.
    """
    if regime is None:
        return "UNKNOWN"
    if isinstance(regime, str):
        return regime.upper()
    if isinstance(regime, dict):
        for key in ("regime", "name", "label", "current", "direction"):
            if key in regime:
                return str(regime[key]).upper()
    return "UNKNOWN"
