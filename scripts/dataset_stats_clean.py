#!/usr/bin/env python3
"""
scripts/dataset_stats_clean.py — WR recalculada sobre dataset limpo.

Le logs/outcomes.clean.jsonl e computa:
- WR global por direction (LONG/SHORT)
- WR x direction x tier canonico
- WR x source
- WR x top 20 symbols normalizados
- WR: SUSPECT_BOOST vs CLEAN (se campo existir)

Saida: PHASE_1_STATS_CLEAN.md (stdout capturado).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

WIN_STATUSES  = {"WIN", "TP1_HIT", "TP2_HIT", "TP3_HIT", "TARGET_HIT"}
LOSS_STATUSES = {"LOSS", "STOP_HIT", "SL_HIT"}


def is_win(status) -> bool | None:
    if not status:
        return None
    s = str(status).upper()
    if s in WIN_STATUSES or "TP" in s or s == "WIN":
        return True
    if s in LOSS_STATUSES or "STOP" in s or "LOSS" in s:
        return False
    return None  # NEUTRAL/EXPIRED/pending


def wr_calc(outcomes):
    wins   = sum(1 for o in outcomes if is_win(o.get("status")) is True)
    losses = sum(1 for o in outcomes if is_win(o.get("status")) is False)
    total  = wins + losses
    if total == 0:
        return None, 0, 0
    return wins / total * 100, wins, losses


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


def section(title: str, lines: list[str]) -> None:
    lines.append(f"## {title}")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "logs/outcomes.clean.jsonl"
    if not Path(path).exists():
        print(f"ERRO: {path} não encontrado", file=sys.stderr)
        return 2

    outcomes = load(path)

    out: list[str] = []
    out.append("# Phase 1 Stats (clean) — WR recalculada")
    out.append("")
    out.append(f"**Source**: `{path}`")
    out.append(f"**Total outcomes**: {len(outcomes):,}")
    out.append("")

    # WR por direction
    out.append("## WR por direction")
    by_dir = defaultdict(list)
    for o in outcomes:
        by_dir[o.get("direction", "?")].append(o)
    out.append("| Direction | WR | W | L | Total |")
    out.append("|---|---|---|---|---|")
    for d in ("LONG", "SHORT", "NO_TRADE", "NEUTRO", "?"):
        if d not in by_dir:
            continue
        wr, w, l = wr_calc(by_dir[d])
        if wr is None:
            continue
        out.append(f"| {d} | {wr:.1f}% | {w} | {l} | {len(by_dir[d])} |")
    out.append("")

    # WR por direction x tier
    out.append("## WR por direction × tier (normalized_tier, n ≥ 10)")
    by_dir_tier = defaultdict(list)
    for o in outcomes:
        key = (o.get("direction", "?"), o.get("normalized_tier", "?"))
        by_dir_tier[key].append(o)
    rows = []
    for (d, t), lst in by_dir_tier.items():
        wr, w, l = wr_calc(lst)
        if wr is None or (w + l) < 10:
            continue
        rows.append((d, t, wr, w, l, len(lst)))
    rows.sort(key=lambda x: (x[0], -x[2]))
    out.append("| Direction | Tier | WR | W | L | Total |")
    out.append("|---|---|---|---|---|---|")
    for d, t, wr, w, l, total in rows:
        out.append(f"| {d} | `{t}` | {wr:.1f}% | {w} | {l} | {total} |")
    out.append("")

    # WR por source
    out.append("## WR por source")
    by_src = defaultdict(list)
    for o in outcomes:
        by_src[o.get("source", "unknown")].append(o)
    out.append("| Source | WR | W | L | Total |")
    out.append("|---|---|---|---|---|")
    for src, lst in sorted(by_src.items(), key=lambda x: -len(x[1])):
        wr, w, l = wr_calc(lst)
        if wr is None:
            continue
        out.append(f"| `{src}` | {wr:.1f}% | {w} | {l} | {len(lst)} |")
    out.append("")

    # WR por symbol top 20
    out.append("## WR por symbol (top 20 por volume)")
    by_sym = defaultdict(list)
    for o in outcomes:
        by_sym[o.get("normalized_symbol", "?")].append(o)
    top20 = sorted(by_sym.items(), key=lambda x: -len(x[1]))[:20]
    out.append("| Symbol | WR | W | L | Total |")
    out.append("|---|---|---|---|---|")
    for sym, lst in top20:
        wr, w, l = wr_calc(lst)
        if wr is None:
            continue
        out.append(f"| `{sym}` | {wr:.1f}% | {w} | {l} | {len(lst)} |")
    out.append("")

    # WR: SUSPECT vs CLEAN
    out.append("## WR: SUSPECT boost vs CLEAN")
    suspect = [o for o in outcomes if o.get("suspect_boost")]
    clean   = [o for o in outcomes if not o.get("suspect_boost")]
    out.append("| Group | Direction | WR | W | L | Total |")
    out.append("|---|---|---|---|---|---|")
    if not suspect:
        out.append("| *(suspect=0 no dataset atual — sem campo `boosts` nos outcomes legado)* | - | - | - | - | - |")
    else:
        for label, lst in [("SUSPECT", suspect), ("CLEAN", clean)]:
            for d in ("LONG", "SHORT"):
                sub = [o for o in lst if o.get("direction") == d]
                wr, w, l = wr_calc(sub)
                if wr is not None:
                    out.append(f"| {label} | {d} | {wr:.1f}% | {w} | {l} | {len(sub)} |")
    out.append("")

    # WR blue chip vs altcoin
    out.append("## WR: Blue chip vs resto")
    BLUE = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
            "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
            "SUIUSDT", "TONUSDT", "MATICUSDT"}
    bc   = [o for o in outcomes if o.get("normalized_symbol") in BLUE]
    alts = [o for o in outcomes if o.get("normalized_symbol") not in BLUE]
    out.append("| Group | WR | W | L | Total |")
    out.append("|---|---|---|---|---|")
    for label, lst in [("BLUE_CHIP", bc), ("ALTCOINS", alts)]:
        wr, w, l = wr_calc(lst)
        if wr is not None:
            out.append(f"| {label} | {wr:.1f}% | {w} | {l} | {len(lst)} |")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
