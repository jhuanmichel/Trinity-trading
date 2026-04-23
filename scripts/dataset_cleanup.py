#!/usr/bin/env python3
"""
scripts/dataset_cleanup.py — Normaliza e filtra outcomes em JSONL.

Lê arquivo (1º arg, default /tmp/outcomes_prod_export.jsonl), gera:
  - logs/outcomes.clean.jsonl (versao normalizada, commitavel)
  - logs/outcomes.cleanup_report.json (métricas da limpeza)

Transformações (não-destrutivas — arquivos originais intocados):
1. `normalized_symbol`: compact uppercase (BTC/USDT:USDT → BTCUSDT)
2. `normalized_tier`: canonical ou 'UNKNOWN' pra string numérica/invalida
3. Filtra outcomes sem symbol OU sem status
4. Dedup por (symbol_normalizado, registered_at[:16], direction)
5. Flag `suspect_boost=True` se total_mult(boosts) > 1.5

Gate: retention ≥ 90%. Senão aborta com exit 1.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

CANONICAL_TIERS = {
    "CRITICAL", "EXTREME", "STRONG", "HIGH", "TRADEABLE",
    "MEDIUM", "WEAK", "MICRO", "BLOCKED",
    "GOLD", "SILVER", "BRONZE", "AVOID",
}


def normalize_symbol(sym) -> str:
    if not sym:
        return ""
    s = str(sym).upper()
    s = re.sub(r":.*$", "", s)   # BTC/USDT:USDT → BTC/USDT
    return s.replace("/", "").replace("_", "").replace("-", "")


def normalize_tier(tier) -> str:
    if tier is None:
        return "UNKNOWN"
    t = str(tier).upper().strip()
    return t if t in CANONICAL_TIERS else "UNKNOWN"


def total_boost(boosts) -> float:
    if not isinstance(boosts, dict):
        return 1.0
    total = 1.0
    for v in boosts.values():
        try:
            total *= float(v)
        except (TypeError, ValueError):
            pass
    return total


def cleanup(input_path: str, output_path: str, report_path: str) -> dict:
    report: dict = {
        "input":                  input_path,
        "output":                 output_path,
        "input_count":            0,
        "output_count":           0,
        "filtered_no_symbol":     0,
        "filtered_no_status":     0,
        "duplicates_removed":     0,
        "tier_corrupted_marked":  0,
        "symbol_normalized":      0,
        "suspect_boost_marked":   0,
        "tier_distribution_clean": Counter(),
        "source_distribution_clean": Counter(),
        "symbol_top20_clean":     Counter(),
    }

    seen_composite: set[str] = set()

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(input_path) as fin, open(output_path, "w") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue

            report["input_count"] += 1

            sym_raw = o.get("symbol", "")
            if not sym_raw:
                report["filtered_no_symbol"] += 1
                continue
            if not o.get("status"):
                report["filtered_no_status"] += 1
                continue

            sym_norm = normalize_symbol(sym_raw)
            if sym_norm != sym_raw:
                report["symbol_normalized"] += 1
            o["normalized_symbol"] = sym_norm

            tier_raw = o.get("conviction_tier") or o.get("tier")
            tier_norm = normalize_tier(tier_raw)
            if tier_norm == "UNKNOWN" and tier_raw:
                report["tier_corrupted_marked"] += 1
            o["normalized_tier"] = tier_norm

            ts_key = (o.get("registered_at") or "")[:16]  # YYYY-MM-DDTHH:MM
            composite = f"{sym_norm}|{ts_key}|{o.get('direction', '')}"
            if composite in seen_composite:
                report["duplicates_removed"] += 1
                continue
            seen_composite.add(composite)

            boosts = o.get("boosts") or o.get("multipliers") or {}
            tm = total_boost(boosts)
            if tm > 1.5:
                o["suspect_boost"] = True
                o["suspect_reason"] = f"chained_multiplier_total={tm:.3f}"
                report["suspect_boost_marked"] += 1

            report["tier_distribution_clean"][tier_norm] += 1
            report["source_distribution_clean"][o.get("source", "unknown")] += 1
            report["symbol_top20_clean"][sym_norm] += 1

            fout.write(json.dumps(o, ensure_ascii=False) + "\n")
            report["output_count"] += 1

    # Serializa counters pra top-N
    report["symbol_top20_clean"] = dict(
        report["symbol_top20_clean"].most_common(20)
    )
    report["tier_distribution_clean"] = dict(report["tier_distribution_clean"])
    report["source_distribution_clean"] = dict(report["source_distribution_clean"])

    with open(report_path, "w") as fr:
        json.dump(report, fr, indent=2)

    return report


def format_report_md(report: dict) -> str:
    lines = []
    add = lines.append
    inp = report["input_count"]
    out = report["output_count"]
    retention = 100 * out / max(inp, 1)

    add("# Phase 1 Cleanup — Dataset Outcomes")
    add("")
    add(f"**Input**: `{report['input']}` — {inp:,} outcomes")
    add(f"**Output**: `{report['output']}` — {out:,} outcomes")
    add(f"**Retention**: {retention:.1f}%")
    add("")

    add("## Filtros aplicados")
    add("| Categoria | N |")
    add("|---|---|")
    add(f"| Filtrados sem symbol | {report['filtered_no_symbol']:,} |")
    add(f"| Filtrados sem status | {report['filtered_no_status']:,} |")
    add(f"| Duplicatas removidas | {report['duplicates_removed']:,} |")
    add("")

    add("## Transformações")
    add("| Categoria | N |")
    add("|---|---|")
    add(f"| Symbols normalizados | {report['symbol_normalized']:,} |")
    add(f"| Tiers corrompidos → UNKNOWN | {report['tier_corrupted_marked']:,} |")
    add(f"| Suspect boost flagged (>1.5x) | {report['suspect_boost_marked']:,} |")
    add("")

    add("## Distribuição tier (clean)")
    add("| Tier | N |")
    add("|---|---|")
    for t, n in sorted(report["tier_distribution_clean"].items(), key=lambda x: -x[1]):
        add(f"| `{t}` | {n:,} |")
    add("")

    add("## Distribuição source (clean)")
    add("| Source | N |")
    add("|---|---|")
    for s, n in sorted(report["source_distribution_clean"].items(), key=lambda x: -x[1]):
        add(f"| `{s}` | {n:,} |")
    add("")

    add("## Top 20 symbols (clean, normalizados)")
    add("| Symbol | N |")
    add("|---|---|")
    for sym, n in list(report["symbol_top20_clean"].items())[:20]:
        add(f"| `{sym}` | {n:,} |")
    add("")

    if retention < 90:
        add(f"## ⚠️ GATE FAIL: retention {retention:.1f}% < 90%")
    else:
        add(f"## ✅ GATE PASS: retention {retention:.1f}% ≥ 90%")

    return "\n".join(lines)


def main() -> int:
    input_path  = sys.argv[1] if len(sys.argv) > 1 else "/tmp/outcomes_prod_export.jsonl"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "logs/outcomes.clean.jsonl"
    report_json = "logs/outcomes.cleanup_report.json"

    if not Path(input_path).exists():
        print(f"ERRO: {input_path} não encontrado", file=sys.stderr)
        return 2

    print(f"Lendo:    {input_path}", file=sys.stderr)
    print(f"Escrevendo: {output_path}", file=sys.stderr)

    report = cleanup(input_path, output_path, report_json)

    md = format_report_md(report)
    print(md)

    retention = 100 * report["output_count"] / max(report["input_count"], 1)
    if retention < 90:
        print(f"\n⚠️ GATE FAIL: retention {retention:.1f}% < 90%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
