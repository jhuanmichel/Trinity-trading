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
