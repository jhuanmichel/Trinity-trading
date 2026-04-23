#!/usr/bin/env python3
"""
scripts/scoring_regression_test.py — Regressao V1 vs V2 sobre dataset limpo.

Limitacao: outcomes nao persistem coin_data (pct_change_24h, funding_rate,
ls_ratio, btc_bias) — logo nao podemos recomputar os boosts V2 exatos.

O que PODEMOS fazer:
  - base_reconstructed = sum(layer_scores)       # 4x componentes 0-25
  - v1_implied_mult    = score / base_reconstructed
  - distribuicao de v1_implied_mult vs V2 caps (1.30 individual, 1.80 total)

Interpretacao: outcomes com v1_implied_mult > 1.80 sao os casos onde
V2 produziria score estritamente menor (cap total mordendo).

Saida: PHASE_2_REGRESSION.md + stdout.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from trinity.scoring.boost_manager import BOOST_CAP_INDIVIDUAL, BOOST_CAP_TOTAL

INPUT_DEFAULT = "logs/outcomes.clean.jsonl"


def load(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def bucket(mult: float) -> str:
    if mult < 1.0:    return "<1.00 (penalty)"
    if mult < 1.10:   return "1.00-1.10"
    if mult < 1.30:   return "1.10-1.30"
    if mult < 1.50:   return "1.30-1.50"
    if mult < 1.80:   return "1.50-1.80"
    if mult < 2.50:   return "1.80-2.50 (V2 capped)"
    return ">=2.50 (V2 heavy cap)"


BUCKET_ORDER = [
    "<1.00 (penalty)",
    "1.00-1.10",
    "1.10-1.30",
    "1.30-1.50",
    "1.50-1.80",
    "1.80-2.50 (V2 capped)",
    ">=2.50 (V2 heavy cap)",
]


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else INPUT_DEFAULT
    if not Path(path).exists():
        print(f"ERRO: {path} nao encontrado", file=sys.stderr)
        return 2

    outcomes = load(path)

    mults: list[float] = []
    mults_by_src: dict[str, list[float]] = defaultdict(list)
    buckets = Counter()
    skipped_no_layers = 0
    skipped_zero_base = 0

    for o in outcomes:
        layers = o.get("layer_scores") or {}
        if not isinstance(layers, dict) or not layers:
            skipped_no_layers += 1
            continue
        base = sum(float(v or 0) for v in layers.values())
        if base <= 0:
            skipped_zero_base += 1
            continue

        v1 = o.get("score")
        if v1 is None:
            continue
        try:
            v1 = float(v1)
        except (TypeError, ValueError):
            continue

        mult = v1 / base
        mults.append(mult)
        src = o.get("source") or "unknown"
        mults_by_src[src].append(mult)
        buckets[bucket(mult)] += 1

    if not mults:
        print("ERRO: nenhum outcome utilizavel (sem layer_scores)", file=sys.stderr)
        return 3

    # Distribuicao
    def pct(p):
        return statistics.quantiles(mults, n=100)[p - 1]

    out: list[str] = []
    out.append("# Phase 2 Regression — V1 implied multiplier vs V2 caps")
    out.append("")
    out.append(f"**Source**: `{path}`")
    out.append(f"**Total outcomes**: {len(outcomes):,}")
    out.append(f"**Utilizaveis (com layer_scores)**: {len(mults):,}")
    out.append(f"**Skipped (sem layers)**: {skipped_no_layers:,}  |  "
               f"**(base zero)**: {skipped_zero_base:,}")
    out.append("")
    out.append(f"**V2 caps**: individual={BOOST_CAP_INDIVIDUAL}, "
               f"total={BOOST_CAP_TOTAL}")
    out.append("")

    out.append("## Distribuicao v1_implied_mult = score / sum(layer_scores)")
    out.append("| Estatistica | Valor |")
    out.append("|---|---|")
    out.append(f"| min    | {min(mults):.3f} |")
    out.append(f"| p10    | {pct(10):.3f} |")
    out.append(f"| p25    | {pct(25):.3f} |")
    out.append(f"| p50    | {statistics.median(mults):.3f} |")
    out.append(f"| mean   | {statistics.fmean(mults):.3f} |")
    out.append(f"| p75    | {pct(75):.3f} |")
    out.append(f"| p90    | {pct(90):.3f} |")
    out.append(f"| p95    | {pct(95):.3f} |")
    out.append(f"| max    | {max(mults):.3f} |")
    out.append("")

    out.append("## Buckets de v1_implied_mult")
    out.append("| Bucket | N | % |")
    out.append("|---|---|---|")
    total = len(mults)
    for b in BUCKET_ORDER:
        n = buckets.get(b, 0)
        if n == 0:
            continue
        out.append(f"| `{b}` | {n:,} | {100 * n / total:.1f}% |")
    out.append("")

    exceed_total = sum(1 for m in mults if m > BOOST_CAP_TOTAL)
    out.append(f"**Outcomes V1 com mult > {BOOST_CAP_TOTAL} "
               f"(capped em V2)**: {exceed_total:,} / {total:,} "
               f"({100 * exceed_total / total:.1f}%)")
    out.append("")

    # Por source
    out.append("## Por source")
    out.append("| Source | N | mean_mult | p50 | p95 | max | %>1.80 |")
    out.append("|---|---|---|---|---|---|---|")
    for src, lst in sorted(mults_by_src.items(), key=lambda x: -len(x[1])):
        if len(lst) < 5:
            continue
        n = len(lst)
        mean = statistics.fmean(lst)
        med  = statistics.median(lst)
        p95  = statistics.quantiles(lst, n=20)[18] if n >= 20 else max(lst)
        mx   = max(lst)
        over = sum(1 for m in lst if m > BOOST_CAP_TOTAL)
        out.append(f"| `{src}` | {n:,} | {mean:.3f} | {med:.3f} | "
                   f"{p95:.3f} | {mx:.3f} | {100 * over / n:.1f}% |")
    out.append("")

    # Interpretacao
    out.append("## Interpretacao")
    out.append("")
    out.append("`v1_implied_mult = outcome.score / sum(layer_scores)` revela "
               "o multiplicador efetivo que V1 aplicou sobre os 4 componentes "
               "base (0-25 cada).")
    out.append("")
    out.append("- **mult < 1.0**: V1 aplicou floor/normalization ou os "
               "componentes foram reduzidos (ex: min(100, score) apos boost).")
    out.append("- **mult em 1.0-1.30**: regime normal; V2 reproduz.")
    out.append("- **mult em 1.30-1.80**: V1 aplicou boosts moderados; V2 "
               "mantem dentro do cap total.")
    out.append(f"- **mult > 1.80 ({exceed_total} outcomes, "
               f"{100 * exceed_total / total:.1f}%)**: V1 encadeou "
               "multiplicadores alem do cap total V2 — estes sao os casos "
               "onde V2 atenua o score explicitamente.")
    out.append("")

    # Gate: < 20% diff
    # Proxy: percentual de outcomes que seriam reduzidos (mult > 1.80)
    pct_capped = 100 * exceed_total / total
    if pct_capped > 20:
        out.append(f"## WARN: {pct_capped:.1f}% outcomes seriam capped em V2 "
                   f"(> 20%). V2 reduz score em larga parcela — investigar "
                   f"caps ou se V1 regime esta saudavel.")
    else:
        out.append(f"## GATE PASS: {pct_capped:.1f}% outcomes capped em V2 "
                   f"(<= 20%). Mudanca localizada a outliers.")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
