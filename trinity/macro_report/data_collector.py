"""
data_collector.py — Coleta dados macro de 8+ fontes para o relatório semanal.

Fontes:
  1. FRED API — NFCI, M2, Fed Balance Sheet, Treasury Yields, DXY proxy
  2. MEXC — BTC price, funding, OI, top movers
  3. Binance — L/S ratio BTC
  4. Trinity Engine — BTC Regime, Bull Band, Sazonalidade
  5. Funding Scanner — top extremos atuais
  6. Web search — ETF flows, VIX, headlines (via requests)

Retorna dict com TODOS os dados coletados, mesmo que incompletos.
Campos faltantes ficam como None — o prompt de análise lida com isso.
"""
import logging
import time
import requests
from datetime import datetime, timezone
from typing import Optional, List

log = logging.getLogger(__name__)

FRED_KEY = ""
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
MEXC_FUTURES = "https://contract.mexc.com"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _get_fred_key() -> str:
    global FRED_KEY
    if FRED_KEY:
        return FRED_KEY
    try:
        import sys, os
        from pathlib import Path
        base = Path(__file__).parent.parent.parent
        if str(base) not in sys.path:
            sys.path.insert(0, str(base))
        from config import FRED_API_KEY
        FRED_KEY = FRED_API_KEY or os.getenv("FRED_API_KEY", "")
    except Exception:
        import os
        FRED_KEY = os.getenv("FRED_API_KEY", "")
    return FRED_KEY


def collect_all() -> dict:
    """
    Coleta todos os dados disponíveis. Cada seção é independente —
    se uma falhar, as outras continuam.

    Returns dict com seções:
        btc_market, macro_fred, regime_trinity, funding_extreme,
        derivatives, top_movers, alt_market, metadata
    """
    t0 = time.time()
    now = datetime.now(timezone.utc)

    data = {
        "metadata": {
            "collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "date_display": now.strftime("%d/%m/%Y %H:%M UTC"),
            "week_number": now.isocalendar()[1],
        },
        "btc_market": {},
        "macro_fred": {},
        "regime_trinity": {},
        "funding_extreme": {},
        "derivatives": {},
        "top_movers": {},
        "alt_market": {},
    }

    # ── 1. BTC Market Data (MEXC) ─────────────────────────────────────────
    try:
        r = requests.get(f"{MEXC_FUTURES}/api/v1/contract/ticker", headers=_HEADERS, timeout=10)
        r.raise_for_status()
        body = r.json()
        tickers = body.get("data", body) if isinstance(body, dict) else body

        btc = next((t for t in tickers if t.get("symbol") == "BTC_USDT"), None)
        if btc:
            price = float(btc.get("lastPrice", 0))
            data["btc_market"] = {
                "price": price,
                "change_24h_pct": round(float(btc.get("riseFallRate", 0)) * 100, 2),
                "high_24h": float(btc.get("high24Price", 0)),
                "low_24h": float(btc.get("lower24Price", btc.get("low24Price", 0))),
                "volume_24h_usd": float(btc.get("amount24", 0)),
                "funding_rate": float(btc.get("fundingRate", 0)),
                "funding_ann": round(float(btc.get("fundingRate", 0)) * 3 * 365 * 100, 1),
                "hold_vol": float(btc.get("holdVol", 0)),
                "oi_usd": round(float(btc.get("holdVol", 0)) * price),
            }

        eth = next((t for t in tickers if t.get("symbol") == "ETH_USDT"), None)
        if eth:
            data["btc_market"]["eth_price"] = float(eth.get("lastPrice", 0))
            data["btc_market"]["eth_change_pct"] = round(float(eth.get("riseFallRate", 0)) * 100, 2)

        # Top movers
        usdt_tickers = [t for t in tickers if t.get("symbol", "").endswith("_USDT")]
        for t in usdt_tickers:
            t["_pct"] = float(t.get("riseFallRate", 0)) * 100

        top_gainers = sorted(usdt_tickers, key=lambda x: x["_pct"], reverse=True)[:10]
        top_losers  = sorted(usdt_tickers, key=lambda x: x["_pct"])[:10]

        data["top_movers"] = {
            "gainers": [{"symbol": t["symbol"].replace("_USDT", ""), "pct": round(t["_pct"], 1),
                         "vol": float(t.get("amount24", 0))} for t in top_gainers],
            "losers":  [{"symbol": t["symbol"].replace("_USDT", ""), "pct": round(t["_pct"], 1),
                         "vol": float(t.get("amount24", 0))} for t in top_losers],
        }

        # Funding extremos
        funding_extremes = sorted(
            [t for t in usdt_tickers if abs(float(t.get("fundingRate", 0))) >= 0.001],
            key=lambda x: abs(float(x.get("fundingRate", 0))), reverse=True
        )[:10]
        data["funding_extreme"] = {
            "count": len(funding_extremes),
            "top": [{"symbol": t["symbol"].replace("_USDT", ""),
                     "rate": round(float(t.get("fundingRate", 0)) * 100, 4),
                     "ann":  round(float(t.get("fundingRate", 0)) * 3 * 365 * 100, 0),
                     "direction": "LONG" if float(t.get("fundingRate", 0)) < 0 else "SHORT"}
                    for t in funding_extremes],
        }

        # Alt market stats
        total_usdt = len(usdt_tickers)
        positive   = sum(1 for t in usdt_tickers if t["_pct"] > 0)
        negative   = sum(1 for t in usdt_tickers if t["_pct"] < 0)
        avg_change = sum(t["_pct"] for t in usdt_tickers) / max(total_usdt, 1)
        data["alt_market"] = {
            "total_contracts": total_usdt,
            "positive_pct":    round(positive / max(total_usdt, 1) * 100, 1),
            "negative_pct":    round(negative / max(total_usdt, 1) * 100, 1),
            "avg_change_pct":  round(avg_change, 2),
        }

    except Exception as e:
        log.warning(f"[DataCollector] MEXC error: {e}")

    # ── 2. FRED Macro Data ────────────────────────────────────────────────
    fred_key = _get_fred_key()
    if fred_key:
        # NFCI
        try:
            nfci = _fetch_fred("NFCI", fred_key, 12)
            if nfci:
                data["macro_fred"]["nfci_current"]   = float(nfci[0]["value"])
                data["macro_fred"]["nfci_prev_week"] = float(nfci[1]["value"]) if len(nfci) > 1 else None
                data["macro_fred"]["nfci_4w_ago"]    = float(nfci[3]["value"]) if len(nfci) > 3 else None
                data["macro_fred"]["nfci_12w_ago"]   = float(nfci[11]["value"]) if len(nfci) > 11 else None
                data["macro_fred"]["nfci_date"]      = nfci[0]["date"]
                delta_1w = float(nfci[0]["value"]) - (float(nfci[1]["value"]) if len(nfci) > 1 else 0)
                data["macro_fred"]["nfci_direction"] = (
                    "LOOSENING" if delta_1w < -0.03 else "TIGHTENING" if delta_1w > 0.03 else "STABLE"
                )
        except Exception as e:
            log.debug(f"[DataCollector] NFCI error: {e}")

        # M2
        try:
            m2 = _fetch_fred("WM2NS", fred_key, 60)
            if m2 and len(m2) >= 52:
                m2_current  = float(m2[0]["value"])
                m2_year_ago = float(m2[51]["value"])
                m2_4w       = float(m2[3]["value"]) if len(m2) > 3 else m2_current
                data["macro_fred"]["m2_current"]    = round(m2_current, 1)
                data["macro_fred"]["m2_yoy_pct"]    = round((m2_current - m2_year_ago) / m2_year_ago * 100, 2)
                data["macro_fred"]["m2_mom_pct"]    = round((m2_current - m2_4w) / m2_4w * 100, 2)
                data["macro_fred"]["m2_direction"]  = "EXPANDING" if m2_current > m2_4w else "CONTRACTING"
        except Exception as e:
            log.debug(f"[DataCollector] M2 error: {e}")

        # Fed Balance Sheet (WALCL)
        try:
            walcl = _fetch_fred("WALCL", fred_key, 8)
            if walcl:
                data["macro_fred"]["fed_balance_sheet"] = round(float(walcl[0]["value"]) / 1e6, 2)  # trilhões
                if len(walcl) > 3:
                    prev = float(walcl[3]["value"]) / 1e6
                    data["macro_fred"]["fed_bs_change_4w"] = round(float(walcl[0]["value"]) / 1e6 - prev, 3)
        except Exception as e:
            log.debug(f"[DataCollector] WALCL error: {e}")

        # Treasury 10Y
        try:
            t10y = _fetch_fred("DGS10", fred_key, 5)
            if t10y:
                data["macro_fred"]["treasury_10y"]      = float(t10y[0]["value"])
                data["macro_fred"]["treasury_10y_date"] = t10y[0]["date"]
        except Exception as e:
            log.debug(f"[DataCollector] DGS10 error: {e}")

        # Treasury 2Y
        try:
            t2y = _fetch_fred("DGS2", fred_key, 5)
            if t2y:
                data["macro_fred"]["treasury_2y"] = float(t2y[0]["value"])
                if data["macro_fred"].get("treasury_10y"):
                    data["macro_fred"]["yield_spread_10y_2y"] = round(
                        data["macro_fred"]["treasury_10y"] - float(t2y[0]["value"]), 3
                    )
        except Exception as e:
            log.debug(f"[DataCollector] DGS2 error: {e}")

        # VIX
        try:
            vix = _fetch_fred("VIXCLS", fred_key, 5)
            if vix:
                data["macro_fred"]["vix"] = float(vix[0]["value"])
        except Exception as e:
            log.debug(f"[DataCollector] VIX error: {e}")

        # High Yield Spread
        try:
            hy = _fetch_fred("BAMLH0A0HYM2", fred_key, 5)
            if hy:
                data["macro_fred"]["hy_spread"] = float(hy[0]["value"])
        except Exception as e:
            log.debug(f"[DataCollector] HY Spread error: {e}")

    # ── 3. Trinity Regime Engine ──────────────────────────────────────────
    try:
        from trinity.traders.btc_regime_monitor import get_btc_regime
        regime = get_btc_regime()
        data["regime_trinity"] = {
            "direction":          regime.get("direction", "NEUTRAL"),
            "bias":               regime.get("bias", "NEUTRAL"),
            "strength":           regime.get("strength", 0),
            "confirmations":      regime.get("confirmations", 0),
            "bull_score":         regime.get("bull_score", 0),
            "bear_score":         regime.get("bear_score", 0),
            "bull_band_sma":      regime.get("bull_band_sma", 0),
            "bull_band_ema":      regime.get("bull_band_ema", 0),
            "bull_band_bias":     regime.get("bull_band_bias", "NEUTRAL"),
            "bull_band_dist":     regime.get("bull_band_dist", 0),
            "nfci_value":         regime.get("nfci_value", 0),
            "nfci_regime":        regime.get("nfci_regime", "NEUTRAL"),
            "m2_yoy_pct":         regime.get("m2_yoy_pct", 0),
            "m2_direction":       regime.get("m2_direction", "STABLE"),
            "seasonality_month":  regime.get("seasonality_month", 0),
            "seasonality_mult":   regime.get("seasonality_mult", 1.0),
            "seasonality_season": regime.get("seasonality_season", "NEUTRAL"),
            "transition":         regime.get("transition", ""),
            "signals":            regime.get("signals", []),
        }
    except Exception as e:
        log.warning(f"[DataCollector] Trinity Regime error: {e}")

    # ── 4. Binance L/S Ratio ──────────────────────────────────────────────
    try:
        r = requests.get(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
            params={"symbol": "BTCUSDT", "period": "4h", "limit": 6},
            headers=_HEADERS, timeout=10,
        )
        if r.status_code == 200:
            ls_data = r.json()
            if ls_data:
                data["derivatives"] = {
                    "btc_ls_ratio":  round(float(ls_data[0].get("longShortRatio", 1)), 3),
                    "btc_long_pct":  round(float(ls_data[0].get("longAccount", 0.5)) * 100, 1),
                    "btc_short_pct": round(float(ls_data[0].get("shortAccount", 0.5)) * 100, 1),
                }
    except Exception as e:
        log.debug(f"[DataCollector] Binance L/S error: {e}")

    # ── Metadata final ────────────────────────────────────────────────────
    data["metadata"]["collection_time_s"] = round(time.time() - t0, 2)
    data["metadata"]["sources_collected"] = sum(
        1 for k in ["btc_market", "macro_fred", "regime_trinity", "funding_extreme", "derivatives", "top_movers"]
        if data.get(k)
    )

    log.info(
        f"[DataCollector] Dados coletados em {data['metadata']['collection_time_s']}s — "
        f"{data['metadata']['sources_collected']} fontes"
    )

    return data


def _fetch_fred(series_id: str, api_key: str, limit: int = 5) -> list:
    """Busca série FRED. Retorna lista do mais recente ao mais antigo."""
    r = requests.get(FRED_BASE, params={
        "series_id": series_id, "api_key": api_key,
        "file_type": "json", "limit": limit, "sort_order": "desc",
    }, headers=_HEADERS, timeout=15)
    if r.status_code != 200:
        return []
    obs = r.json().get("observations", [])
    return [o for o in obs if o.get("value", ".") != "."]
