"""
trinity/outcomes/tiers.py — Single source of truth para conviction tiers.

Contém:
- ConvictionTier enum (9 valores válidos observados em scanners + HCF + cache)
- validate_tier() — guarda contra corrupção (strings numéricas como "27", "37")
"""
from __future__ import annotations

import logging
from enum import Enum

log = logging.getLogger(__name__)


# ── Whitelist canônica ────────────────────────────────────────────────────────
class ConvictionTier(str, Enum):
    """Tiers legitimamente usados no codebase atual."""
    CRITICAL  = "CRITICAL"
    EXTREME   = "EXTREME"
    STRONG    = "STRONG"
    HIGH      = "HIGH"
    TRADEABLE = "TRADEABLE"
    MEDIUM    = "MEDIUM"
    WEAK      = "WEAK"
    MICRO     = "MICRO"
    BLOCKED   = "BLOCKED"


VALID_TIERS: frozenset[str] = frozenset(t.value for t in ConvictionTier)


def validate_tier(tier, strict: bool = False) -> str:
    """
    Valida tier contra whitelist.

    Args:
        tier: valor a validar. Pode ser None (vira "MEDIUM"), string, ou outro tipo.
        strict: se True, levanta ValueError em inválido. Default False = retorna "INVALID".

    Returns:
        String de tier válido, "MEDIUM" (default p/ None), ou "INVALID" (com log warning).
    """
    if tier is None:
        return "MEDIUM"
    if not isinstance(tier, str):
        tier = str(tier)
    if tier in VALID_TIERS:
        return tier
    if strict:
        raise ValueError(
            f"Tier inválido: {tier!r}. Esperado um de: {sorted(VALID_TIERS)}"
        )
    log.warning(
        f"[Tiers] Tier inválido detectado: {tier!r} — gravando como 'INVALID'. "
        "Verificar scoring engine upstream (provavelmente move_classification "
        "ou conviction retornando valor numérico em vez de string semântica)."
    )
    return "INVALID"
