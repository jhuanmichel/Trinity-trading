#!/usr/bin/env python3
"""
Smoke test do OutcomeSampler (Fase X re-apply).

Valida:
1. Taxas de amostragem por banda (100% >=80, 30% 60-79, 10% 35-59, 0% <35)
2. Bucket labels sao atribuidos corretamente
3. Edge cases: score None, negativo, > 100

Executar: python3 scripts/test_outcome_sampler.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from trinity.utils.outcome_sampler import (
    should_register,
    stats_for_dashboard,
)


def test_rates_per_band():
    """Taxas empiricas com amostra grande batem com expected."""
    print("\nTEST 1: taxas por banda (tolerancia ±3pp)")

    N = 10000
    tolerance = 0.03
    test_cases = [
        # (score, expected_rate)
        (85, 1.00),   # alert
        (70, 0.30),   # near
        (50, 0.10),   # low
        (20, 0.00),   # below min
    ]

    ok_all = True
    for score, expected_rate in test_cases:
        accepted = 0
        for i in range(N):
            ok, bucket = should_register(score, signal_id=f"sig_{i}")
            if ok:
                accepted += 1
        actual = accepted / N
        passed = abs(actual - expected_rate) <= tolerance
        mark = "PASS" if passed else "FAIL"
        print(f"  {mark} score={score}: actual={actual:.3f} expected={expected_rate:.3f}")
        if not passed:
            ok_all = False
    return ok_all


def test_bucket_labels():
    """Bucket label correto para amostras aceitas."""
    print("\nTEST 2: bucket labels em amostras aceitas")

    # Coletar buckets observados (rates 1.00 sao deterministicos, outros nem)
    labels = {
        85: "alert",
        79: "near",
        60: "near",
        59: "low",
        35: "low",
    }

    ok_all = True
    for score, expected_label in labels.items():
        # Force signal_id tal que sampling accepts (tentamos varios)
        found = False
        for i in range(200):
            ok, bucket = should_register(score, signal_id=f"test_{i}")
            if ok:
                # Remove sufixo "_sampled_out" nao aplicavel aqui
                mark = "PASS" if bucket == expected_label else "FAIL"
                print(f"  {mark} score={score}: bucket={bucket!r} expected={expected_label!r}")
                found = True
                if bucket != expected_label:
                    ok_all = False
                break
        if not found:
            print(f"  WARN score={score}: nenhuma amostra aceita em 200 tries (baixo rate OK)")
    return ok_all


def test_edge_cases():
    """None, negativo, > 100, NaN."""
    print("\nTEST 3: edge cases")

    ok_all = True

    # None score
    try:
        ok, bucket = should_register(None)
        print(f"  PASS score=None: ok={ok} bucket={bucket!r}")
    except Exception as e:
        print(f"  FAIL score=None: {type(e).__name__}: {e}")
        ok_all = False

    # Negativo
    try:
        ok, bucket = should_register(-10)
        if ok:
            print(f"  FAIL score=-10: aceitou (bucket={bucket!r})")
            ok_all = False
        else:
            print(f"  PASS score=-10: rejected (bucket={bucket!r})")
    except Exception as e:
        print(f"  FAIL score=-10: {type(e).__name__}: {e}")
        ok_all = False

    # > 100 (deve cair em alert)
    try:
        ok, bucket = should_register(150, signal_id="big")
        if ok and bucket == "alert":
            print(f"  PASS score=150: aceitou como alert")
        elif ok:
            print(f"  FAIL score=150: aceitou mas bucket={bucket!r}")
            ok_all = False
        else:
            print(f"  FAIL score=150: rejeitado")
            ok_all = False
    except Exception as e:
        print(f"  FAIL score=150: {type(e).__name__}: {e}")
        ok_all = False

    # String (nao-numero)
    try:
        ok, bucket = should_register("banana")
        if not ok:
            print(f"  PASS score='banana': rejected (bucket={bucket!r})")
        else:
            print(f"  FAIL score='banana': aceitou?!")
            ok_all = False
    except Exception as e:
        print(f"  FAIL score='banana': {type(e).__name__}: {e}")
        ok_all = False

    return ok_all


def test_determinism():
    """Mesmo signal_id sempre decide o mesmo lado."""
    print("\nTEST 4: sampling deterministico")

    ok_all = True
    for score in [70, 50]:
        for sid in ["sym_btc", "sym_eth", "sym_sol"]:
            first_ok, first_bucket = should_register(score, signal_id=sid)
            for _ in range(10):
                ok2, bucket2 = should_register(score, signal_id=sid)
                if ok2 != first_ok or bucket2 != first_bucket:
                    print(f"  FAIL score={score} sid={sid}: varia entre chamadas")
                    ok_all = False
                    break
    if ok_all:
        print("  PASS: determinismo confirmado (mesmo sid → mesmo resultado)")
    return ok_all


def test_stats_endpoint():
    """stats_for_dashboard retorna config estruturada."""
    print("\nTEST 5: stats_for_dashboard")
    cfg = stats_for_dashboard()
    if "min_score" not in cfg or "buckets" not in cfg:
        print(f"  FAIL: keys esperadas ausentes. got={cfg}")
        return False
    if not isinstance(cfg["buckets"], list) or len(cfg["buckets"]) != 3:
        print(f"  FAIL: buckets nao eh lista de 3. got={cfg['buckets']}")
        return False
    labels = [b["label"] for b in cfg["buckets"]]
    if set(labels) != {"alert", "near", "low"}:
        print(f"  FAIL: labels inesperados. got={labels}")
        return False
    print(f"  PASS: config valida ({cfg['min_score']=}, buckets={labels})")
    return True


def main():
    print("=" * 60)
    print("OUTCOME SAMPLER — SMOKE TEST (Fase X re-apply)")
    print("=" * 60)

    results = {
        "rates":        test_rates_per_band(),
        "labels":       test_bucket_labels(),
        "edge_cases":   test_edge_cases(),
        "determinism":  test_determinism(),
        "stats":        test_stats_endpoint(),
    }

    print("\n" + "=" * 60)
    print("RESUMO")
    print("=" * 60)
    for name, ok in results.items():
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}")

    all_ok = all(results.values())
    print("\n" + ("ALL TESTS PASS" if all_ok else "SOME TESTS FAILED"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
