"""
scripts/diag_pump_candidates_hourly.py

Variante do diag Go/No-Go rodada pelo scheduler a cada 1h.
Reusa a logica de filtro do diag_pump_candidates.py e salva snapshot JSON
em /data/diag/ (Render persistent disk) ou logs/diag/ (dev).

Arquivo de saida: pump_candidates_YYYYMMDDTHHMMSS.json

Exportado:
    run_pump_diag() -> dict   (chamado pelo schedule no start.py)

Uso standalone:
    python3 scripts/diag_pump_candidates_hourly.py
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.diag_pump_candidates import (  # noqa: E402
    _load_futures_universe,
    _fetch_spot_tickers,
    _filter_pumps,
    _verdict,
    MIN_PUMP_PCT,
    MIN_VOLUME_USDT,
    GO_MIN,
    GO_MAX,
)

log = logging.getLogger(__name__)


def _out_dir() -> Path:
    """/data/diag/ em Render (persistent disk), logs/diag/ em dev."""
    if Path("/data").exists():
        d = Path("/data/diag")
    else:
        d = ROOT / "logs" / "diag"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_pump_diag() -> dict:
    """
    Executa um snapshot do diagnostico e salva em arquivo.
    Retorna o payload (dict) para quem chamou; loga resumo.
    Nunca levanta excecao — retorna {"status": "error", ...} em caso de falha.
    """
    try:
        ts_utc = datetime.now(timezone.utc)
        stamp  = ts_utc.strftime("%Y%m%dT%H%M%SZ")

        futures_bases, futures_source = _load_futures_universe()
        if not futures_bases:
            payload = {
                "timestamp_utc": ts_utc.isoformat(),
                "status":        "error",
                "reason":        "futures_universe_empty",
            }
            log.warning("[DiagHourly] Futures whitelist indisponivel — skip")
            return payload

        try:
            tickers = _fetch_spot_tickers()
        except Exception as e:
            payload = {
                "timestamp_utc": ts_utc.isoformat(),
                "status":        "error",
                "reason":        f"fetch_failed: {e}",
            }
            log.warning(f"[DiagHourly] Spot fetch falhou: {e}")
            return payload

        pumps     = _filter_pumps(tickers, futures_bases)
        tradeable = [p for p in pumps if p["has_futures"]]
        spot_only = [p for p in pumps if not p["has_futures"]]
        verdict, msg = _verdict(len(tradeable))

        payload = {
            "timestamp_utc":   ts_utc.isoformat(),
            "status":          "ok",
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
            # Mantem so o essencial — analyze precisa de symbol + pct_24h
            "candidates": [
                {
                    "symbol":     p["symbol"],
                    "base":       p["base"],
                    "pct_24h":    p["pct_24h"],
                    "volume_24h": p["volume_24h"],
                    "has_futures": p["has_futures"],
                }
                for p in pumps[:100]
            ],
        }

        out_file = _out_dir() / f"pump_candidates_{stamp}.json"
        out_file.write_text(json.dumps(payload, indent=2))

        log.info(
            f"[DiagHourly] {stamp}: {len(tradeable)} tradeable, "
            f"verdict={verdict}, saved={out_file.name}"
        )
        return payload

    except Exception as e:
        log.error(f"[DiagHourly] Erro inesperado: {e}", exc_info=True)
        return {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "status":        "error",
            "reason":        f"unexpected: {e}",
        }


def _print_summary(payload: dict) -> None:
    """Helper CLI para uso standalone."""
    if payload.get("status") != "ok":
        print(f"[ERROR] {payload.get('reason')}")
        return
    print(f"Tradeable:  {payload['tradeable']}")
    print(f"Verdict:    {payload['verdict']}")
    print(f"Message:    {payload['verdict_message']}")
    print(f"Top 5:")
    for c in payload.get("candidates", [])[:5]:
        mark = "YES" if c["has_futures"] else "NO "
        print(f"  {c['symbol']:<18} {c['pct_24h']:>+8.1f}%  fut={mark}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    payload = run_pump_diag()
    _print_summary(payload)
    sys.exit(0 if payload.get("status") == "ok" else 1)
