"""
scripts/analyze_pump_diag.py

Agrega snapshots salvos por scripts/diag_pump_candidates_hourly.py.
Calcula:
  - mediana, p25, p75 de candidatos tradeaveis por hora
  - histograma por hora-do-dia UTC
  - simbolos que apareceram >=3x (recorrentes)
  - distribuicao de verdicts

Uso:
    python3 scripts/analyze_pump_diag.py
    python3 scripts/analyze_pump_diag.py --dir /data/diag --hours 24
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median, quantiles

ROOT = Path(__file__).resolve().parent.parent


def _default_dir() -> Path:
    """/data/diag em Render, logs/diag localmente."""
    if Path("/data/diag").exists():
        return Path("/data/diag")
    return ROOT / "logs" / "diag"


def _load_snapshots(d: Path, since: datetime | None) -> list[dict]:
    snapshots: list[dict] = []
    if not d.exists():
        return snapshots
    for f in sorted(d.glob("pump_candidates_*.json")):
        try:
            payload = json.loads(f.read_text())
        except Exception:
            continue
        if payload.get("status") != "ok":
            continue
        ts_str = payload.get("timestamp_utc") or ""
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if since and ts < since:
            continue
        payload["_ts"]       = ts
        payload["_filename"] = f.name
        snapshots.append(payload)
    return snapshots


def _percentile_summary(values: list[int]) -> dict:
    if not values:
        return {"n": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None, "mean": None}
    vals = sorted(values)
    qs = quantiles(vals, n=4) if len(vals) >= 4 else [vals[0], vals[len(vals) // 2], vals[-1]]
    return {
        "n":      len(vals),
        "min":    vals[0],
        "p25":    qs[0] if len(qs) >= 1 else vals[0],
        "median": median(vals),
        "p75":    qs[2] if len(qs) >= 3 else vals[-1],
        "max":    vals[-1],
        "mean":   round(sum(vals) / len(vals), 2),
    }


def _histogram_by_hour(snapshots: list[dict]) -> dict[int, dict]:
    """Agrupa snapshots por hora-do-dia UTC (0..23) e sumariza tradeable."""
    buckets: dict[int, list[int]] = defaultdict(list)
    for s in snapshots:
        hour = s["_ts"].hour
        buckets[hour].append(int(s.get("tradeable", 0)))
    out: dict[int, dict] = {}
    for h in range(24):
        out[h] = {
            "n_snapshots": len(buckets.get(h, [])),
            "summary":     _percentile_summary(buckets.get(h, [])),
        }
    return out


def _recurrent_symbols(snapshots: list[dict], min_occurrences: int = 3) -> list[dict]:
    """Conta aparicoes por simbolo (com has_futures=True)."""
    counter: Counter = Counter()
    last_pct: dict[str, float] = {}
    for s in snapshots:
        for c in s.get("candidates", []):
            if not c.get("has_futures"):
                continue
            sym = c.get("symbol")
            if not sym:
                continue
            counter[sym] += 1
            last_pct[sym] = c.get("pct_24h", 0)
    items = [
        {"symbol": sym, "count": n, "last_pct_24h": last_pct.get(sym, 0)}
        for sym, n in counter.items()
        if n >= min_occurrences
    ]
    items.sort(key=lambda x: (-x["count"], -x["last_pct_24h"]))
    return items


def _verdict_distribution(snapshots: list[dict]) -> dict:
    c: Counter = Counter(s.get("verdict", "UNKNOWN") for s in snapshots)
    total = sum(c.values()) or 1
    return {k: {"count": v, "pct": round(100 * v / total, 1)} for k, v in c.most_common()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=str, default=None,
                        help="Diretorio de snapshots (default: /data/diag ou logs/diag)")
    parser.add_argument("--hours", type=int, default=None,
                        help="Considera apenas snapshots das ultimas N horas (default: todos)")
    parser.add_argument("--min-occurrences", type=int, default=3,
                        help="Minimo de aparicoes para entrar na lista de recorrentes (default: 3)")
    args = parser.parse_args()

    d = Path(args.dir) if args.dir else _default_dir()
    since = None
    if args.hours and args.hours > 0:
        since = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    print("=" * 70)
    print("  ANALYZE PUMP DIAG")
    print("=" * 70)
    print(f"\nDir:     {d}")
    print(f"Since:   {since.isoformat() if since else 'all time'}")

    snapshots = _load_snapshots(d, since)
    print(f"Loaded:  {len(snapshots)} snapshots")

    if not snapshots:
        print("\nNenhum snapshot. Rode scripts/diag_pump_candidates_hourly.py primeiro "
              "ou aguarde o scheduler (1h intervalo).")
        return 1

    first = snapshots[0]["_ts"]
    last  = snapshots[-1]["_ts"]
    window_h = (last - first).total_seconds() / 3600 if first != last else 0
    print(f"Window:  {first.isoformat()} -> {last.isoformat()}  (~{window_h:.1f}h)")

    # 1) Percentile summary de tradeable
    tradeable_vals = [int(s.get("tradeable", 0)) for s in snapshots]
    summary = _percentile_summary(tradeable_vals)
    print(f"\n[TRADEABLE PER SNAPSHOT]  (n={summary['n']})")
    print(f"  min/p25/median/p75/max = "
          f"{summary['min']} / {summary['p25']} / {summary['median']} / "
          f"{summary['p75']} / {summary['max']}")
    print(f"  mean = {summary['mean']}")

    # 2) Verdict distribution
    print(f"\n[VERDICT DISTRIBUTION]")
    for v, info in _verdict_distribution(snapshots).items():
        print(f"  {v:<20} {info['count']:>4}  ({info['pct']}%)")

    # 3) Histogram by hour UTC
    hist = _histogram_by_hour(snapshots)
    print(f"\n[HISTOGRAM BY HOUR UTC]  (median tradeable per hour-of-day)")
    print(f"  {'HOUR':>4}  {'N':>3}  {'MED':>4}  {'P25':>4}  {'P75':>4}  {'MAX':>4}  BAR")
    for h in range(24):
        info = hist[h]
        s = info["summary"]
        if info["n_snapshots"] == 0:
            print(f"  {h:>4}  {info['n_snapshots']:>3}   -     -     -     -   (no data)")
            continue
        med = s.get("median", 0) or 0
        bar = "#" * max(0, min(40, int(med)))
        print(f"  {h:>4}  {info['n_snapshots']:>3}  "
              f"{med!s:>4}  {s.get('p25', 0)!s:>4}  "
              f"{s.get('p75', 0)!s:>4}  {s.get('max', 0)!s:>4}  {bar}")

    # 4) Recurrent symbols
    recurrent = _recurrent_symbols(snapshots, min_occurrences=args.min_occurrences)
    print(f"\n[RECURRENT SYMBOLS  >= {args.min_occurrences}x]  (n={len(recurrent)})")
    if not recurrent:
        print("  Nenhum simbolo apareceu com frequencia minima.")
    else:
        print(f"  {'SYMBOL':<18}  {'COUNT':>5}  LAST_PCT")
        for item in recurrent[:30]:
            print(f"  {item['symbol']:<18}  {item['count']:>5}  {item['last_pct_24h']:>+7.1f}%")

    # 5) Salvar JSON resumo
    out_path = ROOT / "scripts" / "analyze_pump_diag_output.json"
    out_path.write_text(json.dumps({
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "dir":               str(d),
        "snapshots_total":   len(snapshots),
        "window_hours":      round(window_h, 2),
        "tradeable_summary": summary,
        "verdict_dist":      _verdict_distribution(snapshots),
        "histogram_by_hour": hist,
        "recurrent_symbols": recurrent,
    }, indent=2, default=str))
    print(f"\n[OUTPUT] {out_path.relative_to(ROOT)}")

    print("\n" + "=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
