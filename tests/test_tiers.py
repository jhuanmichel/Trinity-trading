"""
tests/test_tiers.py — Unit tests para trinity/outcomes/tiers.py.

Cobre §2 (validate_tier) e §3 (compute_empirical_tier, get_tier_metadata).
"""
from __future__ import annotations

import pytest

from trinity.outcomes.tiers import (
    ConvictionTier,
    VALID_TIERS,
    validate_tier,
)


# ── §2 validate_tier ──────────────────────────────────────────────────────────

def test_validate_tier_all_valid_pass_through():
    for t in VALID_TIERS:
        assert validate_tier(t) == t, f"{t} should be valid"


def test_validate_tier_enum_matches_whitelist():
    assert {t.value for t in ConvictionTier} == set(VALID_TIERS)


def test_validate_tier_none_defaults_to_medium():
    assert validate_tier(None) == "MEDIUM"


def test_validate_tier_numeric_string_invalid(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    result = validate_tier("27")
    assert result == "INVALID"
    assert any("inválido" in rec.message.lower() for rec in caplog.records)


def test_validate_tier_numeric_int_invalid():
    # Coerce não-string via str() e depois valida
    assert validate_tier(37) == "INVALID"


def test_validate_tier_bogus_string_invalid():
    assert validate_tier("BOGUS") == "INVALID"


def test_validate_tier_strict_raises():
    with pytest.raises(ValueError):
        validate_tier("27", strict=True)
    with pytest.raises(ValueError):
        validate_tier("BOGUS", strict=True)


def test_validate_tier_strict_passes_valid():
    assert validate_tier("EXTREME", strict=True) == "EXTREME"
    assert validate_tier("MICRO", strict=True) == "MICRO"


def test_validate_tier_whitelist_contents():
    # Garantir que whitelist cobre os 9 tiers observados em callers + HCF + cache
    expected = {
        "CRITICAL", "EXTREME", "STRONG", "HIGH",
        "TRADEABLE", "MEDIUM", "WEAK", "MICRO", "BLOCKED",
    }
    assert set(VALID_TIERS) == expected


# ── §3 compute_empirical_tier / get_tier_metadata ─────────────────────────────

from trinity.outcomes.tiers import (  # noqa: E402
    compute_empirical_tier,
    get_tier_metadata,
    EMPIRICAL_TIERS_V1,
)


def test_empirical_tier_gold_zone():
    # Score 45-49 é GOLD (melhor WR observada: 52.9%)
    assert compute_empirical_tier(47) == "GOLD"
    assert compute_empirical_tier(45) == "GOLD"
    assert compute_empirical_tier(49.9) == "GOLD"


def test_empirical_tier_silver_zone():
    assert compute_empirical_tier(40) == "SILVER"
    assert compute_empirical_tier(44) == "SILVER"
    assert compute_empirical_tier(85) == "SILVER"
    assert compute_empirical_tier(89.9) == "SILVER"


def test_empirical_tier_bronze_zone():
    assert compute_empirical_tier(35) == "BRONZE"
    assert compute_empirical_tier(39.9) == "BRONZE"
    assert compute_empirical_tier(66) == "BRONZE"
    assert compute_empirical_tier(80) == "BRONZE"


def test_empirical_tier_avoid_zone():
    # Zona anti-predictiva: score 50-79 (WR 22-34%)
    assert compute_empirical_tier(55) == "AVOID"
    assert compute_empirical_tier(62) == "AVOID"   # worst volume bucket
    assert compute_empirical_tier(72) == "AVOID"
    assert compute_empirical_tier(78) == "AVOID"


def test_empirical_tier_out_of_range():
    # Score fora de todos buckets (< 35 ou >= 90 ou None) → UNKNOWN
    assert compute_empirical_tier(0) == "UNKNOWN"
    assert compute_empirical_tier(34.9) == "UNKNOWN"
    assert compute_empirical_tier(90) == "UNKNOWN"
    assert compute_empirical_tier(150) == "UNKNOWN"
    assert compute_empirical_tier(None) == "UNKNOWN"


def test_empirical_tier_handles_non_numeric():
    assert compute_empirical_tier("not a number") == "UNKNOWN"
    assert compute_empirical_tier([]) == "UNKNOWN"


def test_empirical_tier_accepts_string_numeric():
    # Caller pode passar "47.5" em vez de float — aceitar
    assert compute_empirical_tier("47.5") == "GOLD"


def test_get_tier_metadata_returns_all_fields():
    meta = get_tier_metadata(47)
    assert meta["empirical_tier"] == "GOLD"
    assert meta["historical_wr"] == 0.529
    assert meta["sample_size"] == 70


def test_get_tier_metadata_none_returns_default():
    meta = get_tier_metadata(None)
    assert meta["empirical_tier"] == "UNKNOWN"
    assert meta["historical_wr"] is None
    assert meta["sample_size"] == 0


def test_empirical_tiers_v1_no_overlap():
    # Nenhum par de buckets deve sobrepor
    for i, a in enumerate(EMPIRICAL_TIERS_V1):
        for b in EMPIRICAL_TIERS_V1[i + 1:]:
            if a.score_max > b.score_min and b.score_max > a.score_min:
                raise AssertionError(
                    f"Overlap: {a.score_min}-{a.score_max} vs {b.score_min}-{b.score_max}"
                )


def test_empirical_tiers_v1_sample_sizes_positive():
    for t in EMPIRICAL_TIERS_V1:
        assert t.sample_size > 0
        assert 0.0 <= t.historical_wr <= 1.0
