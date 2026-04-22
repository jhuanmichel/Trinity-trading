"""
trinity/outcomes/tiers.py — Single source of truth para conviction tiers.

Contém:
- ConvictionTier enum (9 valores válidos observados em scanners + HCF + cache)
- validate_tier() — guarda contra corrupção (strings numéricas como "27", "37")
- EMPIRICAL_TIERS_V1 — mapping score -> tier baseado em WR real (22/abril/2026)
- compute_empirical_tier() + get_tier_metadata() — classificação empírica
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
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


# ── Empirical mapping V1 ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class TierMapping:
    """Intervalo de score mapeado para empirical_tier via WR real."""
    score_min:     float
    score_max:     float
    empirical_tier: str
    historical_wr: float
    sample_size:   int


# Calibração V1 - 22/abril/2026 (n=2776 outcomes; LONG buckets dominantes).
# Ordem NÃO importa — compute() itera até encontrar match.
#
# Observação: dados mostram anti-correlação entre score nominal e WR real.
# Zona "AVOID" (score 50-79) contém 48% do volume histórico com WR 22-34%.
# Zona "GOLD" (score 45-49) é pequena mas alta qualidade (52.9% WR).
EMPIRICAL_TIERS_V1: list = [
    TierMapping(45, 50, "GOLD",   0.529, 70),
    TierMapping(40, 45, "SILVER", 0.496, 129),
    TierMapping(85, 90, "SILVER", 0.457, 35),
    TierMapping(35, 40, "BRONZE", 0.411, 248),
    TierMapping(80, 85, "BRONZE", 0.405, 37),
    TierMapping(65, 70, "BRONZE", 0.360, 175),
    TierMapping(75, 80, "AVOID",  0.342, 79),
    TierMapping(50, 55, "AVOID",  0.314, 35),
    TierMapping(60, 65, "AVOID",  0.268, 441),
    TierMapping(70, 75, "AVOID",  0.238, 63),
    TierMapping(55, 60, "AVOID",  0.223, 282),
]


def compute_empirical_tier(score, tiers=EMPIRICAL_TIERS_V1) -> str:
    """
    Classifica score via mapping empírico (WR real).

    Returns:
        "GOLD" / "SILVER" / "BRONZE" / "AVOID" / "UNKNOWN"
    """
    if score is None:
        return "UNKNOWN"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "UNKNOWN"
    for t in tiers:
        if t.score_min <= s < t.score_max:
            return t.empirical_tier
    return "UNKNOWN"


def get_tier_metadata(score, tiers=EMPIRICAL_TIERS_V1) -> dict:
    """Retorna dict com empirical_tier + historical_wr + sample_size."""
    default = {"empirical_tier": "UNKNOWN", "historical_wr": None, "sample_size": 0}
    if score is None:
        return default
    try:
        s = float(score)
    except (TypeError, ValueError):
        return default
    for t in tiers:
        if t.score_min <= s < t.score_max:
            return {
                "empirical_tier": t.empirical_tier,
                "historical_wr":  t.historical_wr,
                "sample_size":    t.sample_size,
            }
    return default
