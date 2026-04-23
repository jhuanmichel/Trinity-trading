#!/usr/bin/env python3
"""
scripts/dataset_audit.py — Audita outcomes em JSONL e reporta saúde do dataset.

Lê arquivo em 1º argumento (default: /tmp/outcomes_prod_export.jsonl) e reporta:
- Total de outcomes
- Distribuição por source / direction / status / tier / formato de symbol
- Blue chips count
- Outcomes com tier corrompido (não-canônico)
- Outcomes sem symbol ou status
- Outcomes com score fora de range [0-150]
- Duplicados (signal_id / composite symbol+ts+direction)
- Multiplicadores encadeados > 1.5x

Uso:
    curl -s -u Jhuan:Trinity2026 https://trinity-trading.onrender.com/api/outcomes/export \\
        > /tmp/outcomes_prod_export.jsonl
    python3 scripts/dataset_audit.py /tmp/outcomes_prod_export.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

# Tiers canonicos = união de todos vistos legitimamente em Fases anteriores
# (S1 backend tiers + empirical_tier V1 recalibrado)
CANONICAL_TIERS = {
    "CRITICAL", "EXTREME", "STRONG", "HIGH", "TRADEABLE",
    "MEDIUM", "WEAK", "MICRO", "BLOCKED",
    "GOLD", "SILVER", "BRONZE", "AVOID",
    "UNKNOWN",  # valor pós-validate_tier para input inválido
}

BLUE_CHIPS = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "SUIUSDT", "TONUSDT", "MATICUSDT",
}


def detect_symbol_format(sym: str) -> str:
    if not sym:
        return "empty"
    s = str(sym)
    if "/" in s and ":" in s:
        return "ccxt_swap"   # BTC/USDT:USDT
    if "/" in s:
        return "ccxt_spot"   # BTC/USDT
    if "_" in s:
        return "underscore"  # BTC_USDT
    if s.upper().endswith("USDT"):
        return "compact"     # BTCUSDT
    return "other"


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


def audit(input_path: str) -> dict:
    stats: dict = {
        "total": 0,
        "by_source": Counter(),
        "by_direction": Counter(),
        "by_status": Counter(),
        "tier_corrupted": 0,
        "tier_examples": [],
        "tier_distribution": Counter(),
        "missing_symbol": 0,
        "missing_status": 0,
        "symbol_formats": Counter(),
        "symbol_examples": {},
        "score_out_of_range": 0,
        "score_examples": [],
        "duplicates_by_id": 0,
        "duplicates_by_composite": 0,
        "boost_chained": 0,
        "boost_chained_examples": [],
        "no_boost_data": 0,
        "blue_chip_count": 0,
        "bluechip_symbols": Counter(),
    }

    seen_ids: set[str] = set()
    seen_composites: set[str] = set()

    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue

            stats["total"] += 1

            sid = o.get("signal_id") or o.get("id")
            if sid:
                if sid in seen_ids:
                    stats["duplicates_by_id"] += 1
                seen_ids.add(sid)

            sym = o.get("symbol", "")
            if not sym:
                stats["missing_symbol"] += 1
            else:
                fmt = detect_symbol_format(sym)
                stats["symbol_formats"][fmt] += 1
                if fmt not in stats["symbol_examples"]:
                    stats["symbol_examples"][fmt] = sym
                # Normaliza pra blue-chip test
                norm = str(sym).upper().split(":")[0].replace("/", "").replace("_", "")
                if norm in BLUE_CHIPS:
                    stats["blue_chip_count"] += 1
                    stats["bluechip_symbols"][norm] += 1

            if not o.get("status"):
                stats["missing_status"] += 1

            composite = f"{sym}|{o.get('registered_at', '')[:16]}|{o.get('direction', '')}"
            if composite in seen_composites:
                stats["duplicates_by_composite"] += 1
            seen_composites.add(composite)

            src = o.get("source", "unknown")
            stats["by_source"][src] += 1

            d = o.get("direction", "?")
            stats["by_direction"][d] += 1

            s = o.get("status", "?")
            stats["by_status"][s] += 1

            tier = o.get("conviction_tier") or o.get("tier") or ""
            if tier:
                t = str(tier).upper().strip()
                stats["tier_distribution"][t] += 1
                if t not in CANONICAL_TIERS:
                    stats["tier_corrupted"] += 1
                    if len(stats["tier_examples"]) < 10:
                        stats["tier_examples"].append({
                            "symbol": sym,
                            "tier": tier,
                            "source": src,
                            "signal_id": sid,
                        })

            score = o.get("score", 0)
            try:
                score_f = float(score)
                if score_f < 0 or score_f > 150:
                    stats["score_out_of_range"] += 1
                    if len(stats["score_examples"]) < 5:
                        stats["score_examples"].append({
                            "symbol": sym, "score": score_f, "source": src,
                        })
            except (TypeError, ValueError):
                stats["score_out_of_range"] += 1

            boosts = o.get("boosts") or o.get("multipliers") or {}
            if isinstance(boosts, dict) and boosts:
                tm = total_boost(boosts)
                if tm > 1.5:
                    stats["boost_chained"] += 1
                    if len(stats["boost_chained_examples"]) < 5:
                        stats["boost_chained_examples"].append({
                            "symbol": sym, "boosts": boosts, "total_mult": round(tm, 3),
                        })
            else:
                stats["no_boost_data"] += 1

    return stats


def format_report(stats: dict, input_path: str) -> str:
    lines = []
    add = lines.append
    total = stats["total"] or 1

    add("# Phase 1 Audit — Dataset Outcomes")
    add("")
    add(f"**Source**: `{input_path}`")
    add(f"**Total outcomes**: {stats['total']:,}")
    add("")

    add("## Distribuição por source")
    add("| Source | N | % |")
    add("|---|---|---|")
    for src, n in stats["by_source"].most_common():
        add(f"| `{src}` | {n:,} | {100*n/total:.1f}% |")
    add("")

    add("## Distribuição por direction")
    add("| Direction | N | % |")
    add("|---|---|---|")
    for d, n in stats["by_direction"].most_common():
        add(f"| {d} | {n:,} | {100*n/total:.1f}% |")
    add("")

    add("## Distribuição por status")
    add("| Status | N | % |")
    add("|---|---|---|")
    for s, n in stats["by_status"].most_common():
        add(f"| {s} | {n:,} | {100*n/total:.1f}% |")
    add("")

    add("## Distribuição por tier (top 20)")
    add("| Canônico | Tier | N | % |")
    add("|---|---|---|---|")
    for t, n in stats["tier_distribution"].most_common(20):
        mark = "✅" if t in CANONICAL_TIERS else "❌"
        add(f"| {mark} | `{t}` | {n:,} | {100*n/total:.1f}% |")
    add("")

    add("## Formatos de symbol")
    add("| Formato | N | % | Exemplo |")
    add("|---|---|---|---|")
    for fmt, n in stats["symbol_formats"].most_common():
        ex = stats["symbol_examples"].get(fmt, "")
        add(f"| {fmt} | {n:,} | {100*n/total:.1f}% | `{ex}` |")
    add("")

    add("## Blue chips no dataset")
    add(f"**Total blue chip**: {stats['blue_chip_count']:,} "
        f"({100*stats['blue_chip_count']/total:.2f}% do dataset)")
    if stats["bluechip_symbols"]:
        add("")
        add("| Symbol | N |")
        add("|---|---|")
        for sym, n in stats["bluechip_symbols"].most_common():
            add(f"| `{sym}` | {n:,} |")
    else:
        add("")
        add("⚠️ **ZERO blue chips no dataset** — confirma viés altcoin-only")
    add("")

    add("## Problemas detectados")
    add("| Categoria | Count | % |")
    add("|---|---|---|")
    add(f"| Tier corrompido (não-canônico) | {stats['tier_corrupted']:,} | {100*stats['tier_corrupted']/total:.2f}% |")
    add(f"| Symbol ausente | {stats['missing_symbol']:,} | {100*stats['missing_symbol']/total:.2f}% |")
    add(f"| Status ausente | {stats['missing_status']:,} | {100*stats['missing_status']/total:.2f}% |")
    add(f"| Score fora [0-150] | {stats['score_out_of_range']:,} | {100*stats['score_out_of_range']/total:.2f}% |")
    add(f"| Duplicados signal_id | {stats['duplicates_by_id']:,} | {100*stats['duplicates_by_id']/total:.2f}% |")
    add(f"| Duplicados composite | {stats['duplicates_by_composite']:,} | {100*stats['duplicates_by_composite']/total:.2f}% |")
    add(f"| Multiplicadores >1.5x | {stats['boost_chained']:,} | {100*stats['boost_chained']/total:.2f}% |")
    add(f"| Sem dados de boost | {stats['no_boost_data']:,} | {100*stats['no_boost_data']/total:.2f}% |")
    add("")

    if stats["tier_examples"]:
        add("### Exemplos tier corrompido")
        add("| Symbol | Tier | Source |")
        add("|---|---|---|")
        for ex in stats["tier_examples"][:5]:
            add(f"| `{ex['symbol']}` | `{ex['tier']}` | `{ex['source']}` |")
        add("")

    if stats["boost_chained_examples"]:
        add("### Exemplos multiplicadores encadeados")
        for ex in stats["boost_chained_examples"][:3]:
            add(f"- `{ex['symbol']}` total_mult={ex['total_mult']} boosts={ex['boosts']}")
        add("")

    return "\n".join(lines)


def main() -> int:
    input_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/outcomes_prod_export.jsonl"
    if not Path(input_path).exists():
        print(f"ERRO: arquivo não encontrado: {input_path}", file=sys.stderr)
        print(
            "Baixe com:\n"
            "  curl -s -u Jhuan:Trinity2026 "
            "https://trinity-trading.onrender.com/api/outcomes/export "
            f"> {input_path}",
            file=sys.stderr,
        )
        return 2

    print(f"Auditando: {input_path}\n", file=sys.stderr)
    stats = audit(input_path)
    report = format_report(stats, input_path)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
