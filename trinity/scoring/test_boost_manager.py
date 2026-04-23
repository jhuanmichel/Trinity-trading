"""Tests do BoostManager. Rodar: python3 -m trinity.scoring.test_boost_manager"""
from __future__ import annotations

from trinity.scoring.boost_manager import (
    BOOST_CAP_INDIVIDUAL,
    BOOST_CAP_TOTAL,
    ScoreBundle,
)


def test_no_boost() -> None:
    b = ScoreBundle(base=50)
    assert b.final_score() == 50
    assert b.effective_multiplier() == 1.0


def test_single_boost_under_cap() -> None:
    b = ScoreBundle(base=50)
    b.add_boost("momentum", 1.20, "rsi=72")
    assert abs(b.final_score() - 60) < 0.01
    assert b.boosts[0].value == 1.20


def test_single_boost_over_individual_cap() -> None:
    b = ScoreBundle(base=50)
    b.add_boost("overext", 1.70, "pct_change_24h=160%")
    # Cap aplicado individualmente
    assert b.boosts[0].value == BOOST_CAP_INDIVIDUAL
    assert abs(b.final_score() - 50 * BOOST_CAP_INDIVIDUAL) < 0.01


def test_chained_boosts_under_total_cap() -> None:
    b = ScoreBundle(base=50)
    b.add_boost("a", 1.20)
    b.add_boost("b", 1.20)
    # Total = 1.44 < 1.80
    assert abs(b.final_score() - 72) < 0.01
    assert not b.audit()["capped"]


def test_chained_boosts_hit_total_cap() -> None:
    b = ScoreBundle(base=50)
    b.add_boost("a", 1.30)
    b.add_boost("b", 1.30)
    b.add_boost("c", 1.30)
    # Total=2.197 > 1.80 → clamped
    assert b.effective_multiplier() == BOOST_CAP_TOTAL
    assert abs(b.final_score() - 50 * BOOST_CAP_TOTAL) < 0.01
    assert b.audit()["capped"]


def test_audit_structure() -> None:
    b = ScoreBundle(base=50)
    b.add_boost("overext", 1.70, "reason A")
    b.add_boost("mom", 1.20, "reason B")
    audit = b.audit()
    assert audit["base"] == 50.0
    assert len(audit["boosts"]) == 2
    assert audit["boosts"][0]["value"] == 1.30   # capped
    assert audit["boosts"][0]["reason"] == "reason A"
    assert audit["boosts"][1]["value"] == 1.20
    assert abs(audit["final"] - 50 * min(1.30 * 1.20, 1.80)) < 0.01


def test_boost_below_1_clamped_to_1() -> None:
    b = ScoreBundle(base=50)
    b.add_boost("weak_signal", 0.80, "low confidence")
    # Clamped para 1.0 (boosts nao penalizam)
    assert b.boosts[0].value == 1.0
    assert b.final_score() == 50


def _run_all() -> None:
    tests = [fn for name, fn in globals().items() if name.startswith("test_")]
    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  OK  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
