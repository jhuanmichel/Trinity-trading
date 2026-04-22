"""
scripts/diag_pump_candidates.py — Go/No-Go gate para Reversal Hunter v1

Mede quantas moedas pumparam >=100% nas ultimas 24h e tem contrato futuro.
Meta: validar que o universo de trabalho e sensato (3..50 candidatos) antes
de iniciar a implementacao do Reversal Hunter (trinity/modules/reversal_hunter/).

Usa o universo de futuros via CCXT (load_markets swap/linear), com fallback
para a whitelist hardcoded introduzida no FIX G
(trinity/modules/market_movers._FUTURES_FALLBACK).

Uso:
    python3 scripts/diag_pump_candidates.py

Saida:
    stdout: tabela + veredito Go/No-Go
    arquivo: scripts/diag_pump_candidates_output.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests

MEXC_SPOT_TICKER = "https://api.mexc.com/api/v3/ticker/24hr"
MIN_PUMP_PCT     = 100.0      # percent
MIN_VOLUME_USDT  = 500_000
OUT_PATH         = ROOT / "scripts" / "diag_pump_candidates_output.json"

# Go/No-Go thresholds
GO_MIN = 3
GO_MAX = 50


def _load_futures_universe() -> tuple[set[str], str]:
    """
    Retorna (bases, source).
    Tenta CCXT primeiro; se falhar, usa _FUTURES_FALLBACK do market_movers.
    """
    # 1) CCXT (mais completo)
    try:
        import ccxt  # type: ignore
        ex = ccxt.mexc({"options": {"defaultType": "swap"}})
        markets = ex.load_markets()
        bases = {
            m.get("base", "")
            for m in markets.values()
            if (m.get("swap") or m.get("linear")) and m.get("base")
        }
        bases.discard("")
        if len(bases) >= 100:
            return bases, "ccxt"
    except Exception as e:
        print(f"[FUTURES] CCXT falhou: {e}", file=sys.stderr)

    # 2) Fallback hardcoded
    try:
        from trinity.modules.market_movers import _FUTURES_FALLBACK
        return set(_FUTURES_FALLBACK), "fallback_hardcoded"
    except Exception as e:
        print(f"[FUTURES] fallback hardcoded indisponivel: {e}", file=sys.stderr)
        return set(), "none"


def _fetch_spot_tickers() -> list[dict]:
    """Busca lista de tickers USDT da MEXC Spot API."""
    r = requests.get(MEXC_SPOT_TICKER, timeout=30)
    r.raise_for_status()
    raw = r.json()
    if not isinstance(raw, list):
        return []
    return [t for t in raw if str(t.get("symbol", "")).endswith("USDT")]


def _filter_pumps(tickers: list[dict], futures_bases: set[str]) -> list[dict]:
    pumps = []
    for t in tickers:
        try:
            sym = str(t.get("symbol", ""))
            pct = float(t.get("priceChangePercent", 0) or 0) * 100.0  # decimal -> %
            vol = float(t.get("quoteVolume", 0) or 0)
            last = float(t.get("lastPrice", 0) or 0)
            if pct < MIN_PUMP_PCT:
                continue
            if vol < MIN_VOLUME_USDT:
                continue
            base = sym[:-4].upper()  # SOLUSDT -> SOL
            pumps.append({
                "symbol":      sym,
                "base":        base,
                "pct_24h":     round(pct, 2),
                "volume_24h":  round(vol, 0),
                "last_price":  last,
                "has_futures": base in futures_bases,
            })
        except (ValueError, TypeError, KeyError):
            continue
    pumps.sort(key=lambda x: x["pct_24h"], reverse=True)
    return pumps


def _print_header(title: str) -> None:
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _print_top_table(pumps: list[dict], n: int = 30) -> None:
    print(f"\n[TOP {min(n, len(pumps))} CANDIDATOS]")
    header = f"  {'SYMBOL':<18} {'PCT_24H':>10} {'VOL_24H_USD':>16} {'PRICE':>12}  FUT"
    print(header)
    print(f"  {'-'*18} {'-'*10} {'-'*16} {'-'*12}  {'-'*3}")
    for p in pumps[:n]:
        mark = "YES" if p["has_futures"] else "NO "
        vol_str = f"${p['volume_24h']:>14,.0f}"
        pr = p["last_price"]
        if pr >= 1:
            pr_str = f"${pr:>10,.4f}"
        elif pr >= 0.01:
            pr_str = f"${pr:>10,.6f}"
        else:
            pr_str = f"${pr:>10,.8f}"
        print(f"  {p['symbol']:<18} {p['pct_24h']:>+9.1f}% {vol_str:>16} {pr_str}  {mark}")


def _verdict(tradeable: int) -> tuple[str, str]:
    """Retorna (verdict, mensagem)."""
    if tradeable == 0:
        return "NO_GO_ZERO", (
            "Zero candidatos tradeaveis. Mercado calmo agora. "
            "Revalidar em algumas horas ou dias."
        )
    if tradeable < GO_MIN:
        return "NO_GO_LOW", (
            f"{tradeable} candidato(s) tradeavel(eis), abaixo do minimo {GO_MIN}. "
            "Zona ambigua: dados reais existem, mas amostra insuficiente para "
            "validar multiplos detectors. Re-executar periodicamente (ex: diag hourly)."
        )
    if tradeable > GO_MAX:
        return "NO_GO_TOO_MANY", (
            f"{tradeable} candidatos tradeaveis (>{GO_MAX}). "
            "Subir threshold PUMP_THRESHOLD_24H para 150% ou 200% "
            "antes de iniciar implementacao."
        )
    return "GO", (
        f"{tradeable} candidatos tradeaveis (faixa saudavel {GO_MIN}-{GO_MAX}). "
        "Universo adequado para Reversal Hunter v1."
    )


def main() -> int:
    _print_header("DIAGNOSTICO - Candidatos a Reversal Hunter (Go/No-Go)")

    # 1) Whitelist de futuros
    futures_bases, futures_source = _load_futures_universe()
    print(f"\n[FUTURES] Fonte: {futures_source}  |  Bases unicas: {len(futures_bases)}")

    if not futures_bases:
        print("\n[ABORT] Sem whitelist de futuros disponivel. Nao e possivel filtrar.")
        return 2

    # 2) Spot tickers
    try:
        tickers = _fetch_spot_tickers()
    except Exception as e:
        print(f"\n[ABORT] Falha fetch MEXC Spot: {e}")
        return 2
    print(f"[TICKERS] Pares USDT (Spot): {len(tickers)}")

    # 3) Filtro pumps
    pumps = _filter_pumps(tickers, futures_bases)
    tradeable = [p for p in pumps if p["has_futures"]]
    spot_only = [p for p in pumps if not p["has_futures"]]

    print(f"\n[PUMPS >={int(MIN_PUMP_PCT)}% E vol>=${int(MIN_VOLUME_USDT):,}]")
    print(f"  Total:         {len(pumps)}")
    print(f"  Com futures:   {len(tradeable)}  (TRADEAVEIS)")
    print(f"  Sem futures:   {len(spot_only)} (descartaveis)")

    # 4) Top table
    _print_top_table(pumps, n=30)

    # 5) Veredito
    verdict, msg = _verdict(len(tradeable))
    print(f"\n[VERDICT] {verdict}")
    print(f"  {msg}")

    # 6) Salvar JSON
    payload = {
        "timestamp_utc":   __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "futures_source":  futures_source,
        "futures_bases":   len(futures_bases),
        "tickers_usdt":    len(tickers),
        "total_pumps":     len(pumps),
        "tradeable":       len(tradeable),
        "spot_only":       len(spot_only),
        "verdict":         verdict,
        "verdict_message": msg,
        "thresholds": {
            "min_pump_pct":   MIN_PUMP_PCT,
            "min_volume_usdt": MIN_VOLUME_USDT,
            "go_range":       [GO_MIN, GO_MAX],
        },
        "candidates_top50": pumps[:50],
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\n[OUTPUT] Salvo em {OUT_PATH.relative_to(ROOT)}")

    print()
    print("=" * 70)
    if verdict == "GO":
        print("  Pode prosseguir com planejamento da Sessao 1 do Reversal Hunter.")
    else:
        print("  NAO iniciar implementacao. Revalidar conforme mensagem acima.")
    print("=" * 70)
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
