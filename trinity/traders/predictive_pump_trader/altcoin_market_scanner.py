"""
altcoin_market_scanner.py — Scanner MEXC Futures (Cap. 20)

Escaneia altcoins em MEXC Linear Perpetuals filtrando candidatos de pump.
Migrado de Bybit V5 → MEXC Futures (Bybit/Binance bloqueados no Render/AWS).

MEXC Futures endpoints (públicos, sem API key):
  Base: https://contract.mexc.com/api/v1/contract
  - GET /ticker                     → todos os tickers
  - GET /ticker?symbol=BTC_USDT     → ticker único
  - GET /kline/{symbol}?interval=Min15&start=&end=
  - GET /openInterest?symbol=BTC_USDT
  - GET /deals?symbol=BTC_USDT&limit=100
  - GET /depth?symbol=BTC_USDT&limit=50  (pode retornar 403, defaulta para vazio)

Formato de símbolo MEXC: BTC_USDT (underscore) — convertido para BTCUSDT na saída.

Filtros de pump candidates:
  - Volume > $10M/24h (liquidez mínima)
  - Price change entre -20% e +3% (caiu mas ainda não explodiu)
  - Alta pump_potential = queda moderada + alto volume
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
MEXC_CONTRACT        = "https://contract.mexc.com/api/v1/contract"
UNIVERSE_CACHE_TTL   = 300
HOT_SCAN_TOP_N       = 50
REQUEST_TIMEOUT      = 10
MAX_WORKERS          = 10
EXCLUDE_SYMBOLS      = {
    "BTCUSDT", "ETHUSDT",
    "DEFIUSDT", "ALTUSDT",
}

MIN_VOLUME_24H_USD   = 10_000_000
MAX_PRICE_CHANGE_PCT = 3.0

_universe_cache: dict = {"data": [], "ts": 0.0}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)",
    "Accept": "application/json",
}


# ── Helpers de símbolo ────────────────────────────────────────────────────────

def _to_mexc(symbol: str) -> str:
    """BTCUSDT → BTC_USDT"""
    if symbol.endswith("USDT") and "_" not in symbol:
        return symbol[:-4] + "_USDT"
    return symbol


def _from_mexc(symbol: str) -> str:
    """BTC_USDT → BTCUSDT"""
    return symbol.replace("_", "")


# ── Public API ────────────────────────────────────────────────────────────────

def scan_universe() -> List[dict]:
    """Retorna top 50 pump candidates filtrados (cache 5min)."""
    now = time.time()
    if _universe_cache["data"] and (now - _universe_cache["ts"]) < UNIVERSE_CACHE_TTL:
        return _universe_cache["data"]

    log.info("[PumpScanner] Atualizando universo MEXC Futures...")
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
        mexc_sym = _to_mexc(symbol)
        ticker   = _get_ticker(mexc_sym)
        funding  = float(ticker.get("fundingRate", 0)) if ticker else 0.0
        oi       = _get_open_interest(mexc_sym)
        ob       = _get_orderbook(mexc_sym)
        klines   = _get_klines(mexc_sym)
        trades   = _get_recent_trades(mexc_sym)

        if not ticker:
            return None

        price     = float(ticker.get("lastPrice", 0))
        oi_usd    = float(oi) * price if oi else 0.0
        oi_change = _estimate_oi_change(symbol, oi_usd)

        vol_24h = float(ticker.get("turnover24", 0) or ticker.get("volume24", 0) or 0)

        return {
            "symbol":           symbol,
            "price":            price,
            "price_change_pct": float(ticker.get("riseFallRate", 0)) * 100,
            "volume_24h":       vol_24h,
            "high_24h":         float(ticker.get("highPrice24", price)),
            "low_24h":          float(ticker.get("lowPrice24", price)),
            "funding_rate":     funding,
            "open_interest":    oi_usd,
            "oi_change_pct":    oi_change,
            "long_short_ratio": 1.0,   # não disponível publicamente no MEXC
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
            f"{MEXC_CONTRACT}/ticker",
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        raw = r.json()
        tickers = raw.get("data", raw) if isinstance(raw, dict) else raw
        if not isinstance(tickers, list):
            log.error(f"[PumpScanner] Formato inesperado do ticker MEXC: {type(tickers)}")
            return []
        return [
            t for t in tickers
            if isinstance(t, dict)
            and t.get("symbol", "").endswith("_USDT")
            and _from_mexc(t.get("symbol", "")) not in EXCLUDE_SYMBOLS
        ]
    except Exception as e:
        log.error(f"[PumpScanner] Erro ao buscar universo MEXC: {e}")
        return []


def _filter_pump_candidates(tickers: List[dict]) -> List[dict]:
    candidates = []
    for t in tickers:
        try:
            symbol     = _from_mexc(t.get("symbol", ""))
            vol_usd    = float(t.get("turnover24", 0) or t.get("volume24", 0) or 0)
            pct_change = float(t.get("riseFallRate", 0)) * 100

            if vol_usd < MIN_VOLUME_24H_USD:
                continue
            if pct_change > MAX_PRICE_CHANGE_PCT:
                continue
            if pct_change < -20.0:
                continue

            drop_score     = max(0, -pct_change) * 2.0
            volume_score   = min(10.0, vol_usd / 100_000_000)
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


def _get_ticker(mexc_symbol: str) -> dict:
    try:
        r = requests.get(
            f"{MEXC_CONTRACT}/ticker",
            params={"symbol": mexc_symbol},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        raw  = r.json()
        data = raw.get("data", raw) if isinstance(raw, dict) else raw
        if isinstance(data, list):
            return data[0] if data else {}
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        log.debug(f"[PumpScanner] Ticker error {mexc_symbol}: {e}")
        return {}


def _get_open_interest(mexc_symbol: str) -> float:
    try:
        r = requests.get(
            f"{MEXC_CONTRACT}/openInterest",
            params={"symbol": mexc_symbol},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        raw  = r.json()
        data = raw.get("data", {}) if isinstance(raw, dict) else {}
        return float(data.get("openInterest", 0) if isinstance(data, dict) else 0)
    except Exception:
        return 0.0


def _get_orderbook(mexc_symbol: str) -> dict:
    """Orderbook MEXC Futures — pode retornar 403, defaulta para vazio."""
    try:
        r = requests.get(
            f"{MEXC_CONTRACT}/depth",
            params={"symbol": mexc_symbol, "limit": 50},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 403:
            return {"bids": [], "asks": []}
        r.raise_for_status()
        raw  = r.json()
        data = raw.get("data", {}) if isinstance(raw, dict) else {}
        bids = data.get("bids", []) if isinstance(data, dict) else []
        asks = data.get("asks", []) if isinstance(data, dict) else []
        return {"bids": bids, "asks": asks}
    except Exception:
        return {"bids": [], "asks": []}


def _get_klines(mexc_symbol: str, interval: str = "Min15", limit: int = 48) -> list:
    """
    MEXC klines — formato de arrays paralelas:
    {"data": {"time": [...], "open": [...], "high": [...], "low": [...], "close": [...], "vol": [...]}}
    Converte para [[ts, o, h, l, c, v], ...]
    """
    try:
        end   = int(time.time())
        start = end - limit * 15 * 60
        r = requests.get(
            f"{MEXC_CONTRACT}/kline/{mexc_symbol}",
            params={"interval": interval, "start": start, "end": end},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        raw  = r.json()
        data = raw.get("data", {}) if isinstance(raw, dict) else {}
        if not isinstance(data, dict):
            return []
        times  = data.get("time",  [])
        opens  = data.get("open",  [])
        highs  = data.get("high",  [])
        lows   = data.get("low",   [])
        closes = data.get("close", [])
        vols   = data.get("vol",   [])
        return [
            [str(t), str(o), str(h), str(l), str(c), str(v)]
            for t, o, h, l, c, v in zip(times, opens, highs, lows, closes, vols)
        ]
    except Exception:
        return []


def _get_recent_trades(mexc_symbol: str, limit: int = 100) -> list:
    try:
        r = requests.get(
            f"{MEXC_CONTRACT}/deals",
            params={"symbol": mexc_symbol, "limit": limit},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        raw  = r.json()
        data = raw.get("data", {}) if isinstance(raw, dict) else {}
        lst  = data.get("dataList", data) if isinstance(data, dict) else data
        if not isinstance(lst, list):
            return []
        return [
            {
                "p": t.get("p", t.get("price", 0)),
                "q": t.get("v", t.get("vol", t.get("size", 0))),
                "m": t.get("T", t.get("side", 2)) == 2,  # MEXC: T=1 buy, T=2 sell
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
