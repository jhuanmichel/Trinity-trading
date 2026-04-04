"""
altcoin_market_scanner.py — Scanner de Pump Candidates (Cap. 20)

Escaneia o universo de altcoins em Bybit Linear Perpetuals filtrando candidatos
com maior probabilidade de pump.

Filtros de pump candidates:
  - Volume > $10M/24h (liquidez mínima)
  - Price change entre -20% e +3% (caiu mas ainda não explodiu)
  - Alta pump_potential = queda moderada + alto volume

Bybit V5 endpoints (públicos, sem API key, sem geo-block em cloud):
  Base: https://api.bybit.com/v5
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BYBIT_V5             = "https://api.bybit.com/v5"
UNIVERSE_CACHE_TTL   = 300
HOT_SCAN_TOP_N       = 50
REQUEST_TIMEOUT      = 8
MAX_WORKERS          = 10
EXCLUDE_SYMBOLS      = {
    "BTCUSDT", "ETHUSDT",
    "DEFIUSDT", "ALTUSDT",
}

MIN_VOLUME_24H_USD   = 10_000_000
MAX_PRICE_CHANGE_PCT = 3.0
FUNDING_SQUEEZE_THRESH = -0.0002

_universe_cache: dict = {"data": [], "ts": 0.0}


# ── Public API ────────────────────────────────────────────────────────────────

def scan_universe() -> List[dict]:
    """Retorna top 50 pump candidates filtrados (cache 5min)."""
    now = time.time()
    if _universe_cache["data"] and (now - _universe_cache["ts"]) < UNIVERSE_CACHE_TTL:
        return _universe_cache["data"]

    log.info("[PumpScanner] Atualizando universo Bybit Linear...")
    raw = _fetch_universe()
    if not raw:
        log.warning("[PumpScanner] Universo vazio — retornando cache")
        return _universe_cache["data"]

    hot = _filter_pump_candidates(raw)
    _universe_cache.update({"data": hot, "ts": now})
    log.info(f"[PumpScanner] {len(raw)} pares → {len(hot)} pump candidates")
    return hot


def fetch_coin_data(symbol: str) -> Optional[dict]:
    """Busca dados completos de um símbolo para análise de pump."""
    try:
        ticker   = _get_ticker(symbol)
        funding  = _get_funding_rate(symbol)
        oi       = _get_open_interest(symbol)
        ls_ratio = _get_long_short_ratio(symbol)
        ob       = _get_orderbook(symbol)
        klines   = _get_klines(symbol)
        trades   = _get_recent_trades(symbol)

        if not ticker:
            return None

        price     = float(ticker.get("lastPrice", 0))
        oi_usd    = float(oi) * price if oi else 0.0
        oi_change = _estimate_oi_change(symbol, oi_usd)

        return {
            "symbol":           symbol,
            "price":            price,
            "price_change_pct": float(ticker.get("price24hPcnt", 0)) * 100,
            "volume_24h":       float(ticker.get("turnover24h", 0)),
            "high_24h":         float(ticker.get("highPrice24h", price)),
            "low_24h":          float(ticker.get("lowPrice24h", price)),
            "funding_rate":     funding,
            "open_interest":    oi_usd,
            "oi_change_pct":    oi_change,
            "long_short_ratio": ls_ratio,
            "orderbook":        ob,
            "klines_15m":       klines,
            "recent_trades":    trades,
        }
    except Exception as e:
        log.warning(f"[PumpScanner] Erro ao buscar dados de {symbol}: {e}")
        return None


def fetch_batch(symbols: List[str], max_workers: int = MAX_WORKERS) -> List[dict]:
    """Busca dados de múltiplos símbolos em paralelo."""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_coin_data, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            data = fut.result()
            if data:
                results.append(data)
    return results


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fetch_universe() -> List[dict]:
    try:
        r = requests.get(
            f"{BYBIT_V5}/market/tickers",
            params={"category": "linear"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        tickers = r.json().get("result", {}).get("list", [])
        return [
            t for t in tickers
            if isinstance(t, dict)
            and t.get("symbol", "").endswith("USDT")
            and t.get("symbol") not in EXCLUDE_SYMBOLS
        ]
    except Exception as e:
        log.error(f"[PumpScanner] Erro ao buscar universo Bybit: {e}")
        return []


def _filter_pump_candidates(tickers: List[dict]) -> List[dict]:
    candidates = []
    for t in tickers:
        try:
            symbol     = t.get("symbol", "")
            vol_usd    = float(t.get("turnover24h", 0))
            pct_change = float(t.get("price24hPcnt", 0)) * 100

            if vol_usd < MIN_VOLUME_24H_USD:
                continue
            if pct_change > MAX_PRICE_CHANGE_PCT:
                continue
            if pct_change < -20.0:
                continue

            drop_score   = max(0, -pct_change) * 2.0
            volume_score = min(10.0, vol_usd / 100_000_000)
            pump_potential = drop_score + volume_score

            candidates.append({
                "symbol":         symbol,
                "price":          float(t.get("lastPrice", 0)),
                "price_change":   pct_change,
                "volume_24h":     vol_usd,
                "pump_potential": pump_potential,
            })
        except (ValueError, TypeError):
            continue

    candidates.sort(key=lambda x: x["pump_potential"], reverse=True)
    return candidates[:HOT_SCAN_TOP_N]


def _get_ticker(symbol: str) -> dict:
    r = requests.get(
        f"{BYBIT_V5}/market/tickers",
        params={"category": "linear", "symbol": symbol},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    lst = r.json().get("result", {}).get("list", [])
    return lst[0] if lst else {}


def _get_funding_rate(symbol: str) -> float:
    try:
        r = requests.get(
            f"{BYBIT_V5}/market/funding/history",
            params={"category": "linear", "symbol": symbol, "limit": 1},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        lst = r.json().get("result", {}).get("list", [])
        return float(lst[0].get("fundingRate", 0)) if lst else 0.0
    except Exception:
        return 0.0


def _get_open_interest(symbol: str) -> float:
    try:
        r = requests.get(
            f"{BYBIT_V5}/market/open-interest",
            params={"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 1},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        lst = r.json().get("result", {}).get("list", [])
        return float(lst[0].get("openInterest", 0)) if lst else 0.0
    except Exception:
        return 0.0


def _get_long_short_ratio(symbol: str) -> float:
    try:
        r = requests.get(
            f"{BYBIT_V5}/market/account-ratio",
            params={"category": "linear", "symbol": symbol, "period": "5min", "limit": 1},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        lst = r.json().get("result", {}).get("list", [])
        if lst:
            buy  = float(lst[0].get("buyRatio", 0.5))
            sell = float(lst[0].get("sellRatio", 0.5))
            return round(buy / sell, 4) if sell > 0 else 1.0
        return 1.0
    except Exception:
        return 1.0


def _get_orderbook(symbol: str) -> dict:
    try:
        r = requests.get(
            f"{BYBIT_V5}/market/orderbook",
            params={"category": "linear", "symbol": symbol, "limit": 50},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        return {"bids": result.get("b", []), "asks": result.get("a", [])}
    except Exception:
        return {"bids": [], "asks": []}


def _get_klines(symbol: str, interval: str = "15", limit: int = 48) -> list:
    try:
        r = requests.get(
            f"{BYBIT_V5}/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        lst = r.json().get("result", {}).get("list", [])
        return [[k[0], k[1], k[2], k[3], k[4], k[5]] for k in lst]
    except Exception:
        return []


def _get_recent_trades(symbol: str, limit: int = 100) -> list:
    try:
        r = requests.get(
            f"{BYBIT_V5}/market/recent-trade",
            params={"category": "linear", "symbol": symbol, "limit": limit},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        lst = r.json().get("result", {}).get("list", [])
        return [
            {
                "p": t.get("price", 0),
                "q": t.get("size", 0),
                "m": t.get("side", "Buy") == "Sell",
            }
            for t in lst
        ]
    except Exception:
        return []


_oi_prev_cache: dict = {}


def _estimate_oi_change(symbol: str, current_oi_usd: float) -> float:
    prev = _oi_prev_cache.get(symbol, current_oi_usd)
    _oi_prev_cache[symbol] = current_oi_usd
    if prev == 0:
        return 0.0
    return round((current_oi_usd - prev) / prev * 100, 2)
