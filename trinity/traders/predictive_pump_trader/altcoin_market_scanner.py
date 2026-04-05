"""
altcoin_market_scanner.py — Scanner Gate.io Futures (Cap. 20)

Migrado: Binance FAPI → Bybit V5 → MEXC Futures → Gate.io Futures
Gate.io: baseado nas Ilhas Cayman, sem geo-block em IPs AWS/Render.

Gate.io Futures endpoints (públicos, sem API key):
  Base: https://api.gateio.ws/api/v4/futures/usdt
  - GET /tickers                      → todos os tickers USDT
  - GET /tickers?contract=SOL_USDT    → ticker único
  - GET /candlesticks?contract=SOL_USDT&interval=15m&limit=48
  - GET /trades?contract=SOL_USDT&limit=100
  - GET /order_book?contract=SOL_USDT&limit=50&interval=0

Filtros de pump candidates:
  - Volume > $10M/24h (liquidez mínima)
  - Price change entre -20% e +3% (caiu mas ainda não explodiu)
  - pump_potential = queda moderada + alto volume
"""
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
GATEIO_FUTURES       = "https://api.gateio.ws/api/v4/futures/usdt"
MEXC_SPOT            = "https://api.mexc.com/api/v3"
CG_BASE              = "https://open-api-v3.coinglass.com/api"

UNIVERSE_CACHE_TTL   = 300
HOT_SCAN_TOP_N       = 50
REQUEST_TIMEOUT      = 10
MAX_WORKERS          = 10
ENRICH_CACHE_TTL     = 300

EXCLUDE_SYMBOLS      = {
    "BTCUSDT", "ETHUSDT",
    "DEFIUSDT", "ALTUSDT",
}

MIN_VOLUME_24H_USD   = 10_000_000
MAX_PRICE_CHANGE_PCT = 3.0

_universe_cache: dict = {"data": [], "ts": 0.0}
_ls_cache:       dict = {}
_fund_cache:     dict = {}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)",
    "Accept": "application/json",
}


def _cg_key() -> str:
    try:
        _base = Path(__file__).parent.parent.parent.parent
        if str(_base) not in sys.path:
            sys.path.insert(0, str(_base))
        from config import COINGLASS_API_KEY  # type: ignore
        return COINGLASS_API_KEY or ""
    except Exception:
        return os.getenv("COINGLASS_API_KEY", "")


# ── Helpers de símbolo ────────────────────────────────────────────────────────

def _to_gate(symbol: str) -> str:
    """SOLUSDT → SOL_USDT"""
    if symbol.endswith("USDT") and "_" not in symbol:
        return symbol[:-4] + "_USDT"
    return symbol


def _from_gate(symbol: str) -> str:
    """SOL_USDT → SOLUSDT"""
    return symbol.replace("_", "")


def _to_cg(symbol: str) -> str:
    """SOLUSDT → SOL"""
    return symbol.replace("USDT", "").replace("_", "")


# ── Public API ────────────────────────────────────────────────────────────────

def scan_universe() -> List[dict]:
    """Retorna top 50 pump candidates filtrados (cache 5min)."""
    now = time.time()
    if _universe_cache["data"] and (now - _universe_cache["ts"]) < UNIVERSE_CACHE_TTL:
        return _universe_cache["data"]

    log.info("[PumpScanner] Atualizando universo Gate.io Futures...")
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
        gate_sym = _to_gate(symbol)

        ticker = _get_ticker(gate_sym)
        if not ticker:
            return None

        price          = float(ticker.get("last", 0))
        oi_contracts   = float(ticker.get("total_size", 0))
        oi_usd         = oi_contracts * price
        oi_change      = _estimate_oi_change(symbol, oi_usd)
        vol_futures    = float(ticker.get("volume_24h_quote", 0))
        funding_single = float(ticker.get("funding_rate", 0))

        ob     = _get_orderbook(gate_sym)
        klines = _get_klines(gate_sym)
        trades = _get_recent_trades(gate_sym)

        # ── Enriquecimento multi-fonte ──────────────────────────────────────
        key        = _cg_key()
        ls_ratio   = _get_ls_ratio_cg(symbol, key)
        fund_multi = _get_funding_multi_cg(symbol, key)
        spot_vol   = _get_spot_volume_mexc(symbol)

        funding_final = fund_multi if fund_multi is not None else funding_single
        spot_futures  = round(vol_futures / spot_vol, 2) if spot_vol > 0 else 1.0

        return {
            "symbol":             symbol,
            "price":              price,
            "price_change_pct":   float(ticker.get("change_percentage", 0)),
            "volume_24h":         vol_futures,
            "high_24h":           float(ticker.get("high_24h", price)),
            "low_24h":            float(ticker.get("low_24h", price)),
            "funding_rate":       funding_final,
            "open_interest":      oi_usd,
            "oi_change_pct":      oi_change,
            "long_short_ratio":   ls_ratio,
            "orderbook":          ob,
            "klines_15m":         klines,
            "recent_trades":      trades,
            "spot_volume_24h":    spot_vol,
            "spot_futures_ratio": spot_futures,
            "funding_single_ex":  funding_single,
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


# ── Enriquecimento multi-fonte ────────────────────────────────────────────────

def _get_ls_ratio_cg(symbol: str, key: str) -> float:
    now = time.time()
    cached = _ls_cache.get(symbol)
    if cached and (now - cached["ts"]) < ENRICH_CACHE_TTL:
        return cached["v"]
    if not key:
        return 1.0
    coin = _to_cg(symbol)
    try:
        r = requests.get(
            f"{CG_BASE}/futures/longShort/aggregatedAccountsRatio",
            params={"symbol": coin, "timeframe": "5m", "limit": 1},
            headers={"CG-API-KEY": key},
            timeout=8,
        )
        if r.status_code == 429:
            return _ls_cache.get(symbol, {}).get("v", 1.0)
        if r.status_code != 200:
            return 1.0
        data = r.json().get("data", [])
        item = (data[0] if isinstance(data, list) and data else
                data   if isinstance(data, dict) else None)
        if item:
            long_pct  = float(item.get("longAccount",  item.get("longRatio",  0.5)))
            short_pct = float(item.get("shortAccount", item.get("shortRatio", 0.5)))
            if short_pct > 0:
                ratio = round(long_pct / short_pct, 4)
                _ls_cache[symbol] = {"v": ratio, "ts": now}
                return ratio
    except Exception as e:
        log.debug(f"[PumpScanner] CoinGlass L/S error {symbol}: {e}")
    return 1.0


def _get_funding_multi_cg(symbol: str, key: str) -> Optional[float]:
    now = time.time()
    cached = _fund_cache.get(symbol)
    if cached and (now - cached["ts"]) < ENRICH_CACHE_TTL:
        return cached["v"]
    if not key:
        return None
    coin = _to_cg(symbol)
    try:
        r = requests.get(
            f"{CG_BASE}/futures/fundingRate/oeRates",
            params={"symbol": coin},
            headers={"CG-API-KEY": key},
            timeout=8,
        )
        if r.status_code == 429:
            return _fund_cache.get(symbol, {}).get("v")
        if r.status_code != 200:
            return None
        data = r.json().get("data", [])
        if isinstance(data, list) and data:
            rates = [
                float(item.get("fundingRate", item.get("rate", 0)))
                for item in data
                if isinstance(item, dict) and ("fundingRate" in item or "rate" in item)
            ]
            if rates:
                avg = sum(rates) / len(rates)
                _fund_cache[symbol] = {"v": avg, "ts": now}
                return avg
    except Exception as e:
        log.debug(f"[PumpScanner] CoinGlass funding error {symbol}: {e}")
    return None


def _get_spot_volume_mexc(symbol: str) -> float:
    try:
        r = requests.get(
            f"{MEXC_SPOT}/ticker/24hr",
            params={"symbol": symbol},
            headers=_HEADERS,
            timeout=5,
        )
        r.raise_for_status()
        return float(r.json().get("quoteVolume", 0))
    except Exception:
        return 0.0


# ── Gate.io Futures helpers ───────────────────────────────────────────────────

def _fetch_universe() -> List[dict]:
    try:
        r = requests.get(
            f"{GATEIO_FUTURES}/tickers",
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        tickers = r.json()
        if not isinstance(tickers, list):
            log.error(f"[PumpScanner] Formato inesperado Gate.io: {type(tickers)}")
            return []
        return [
            t for t in tickers
            if isinstance(t, dict)
            and t.get("contract", "").endswith("_USDT")
            and _from_gate(t.get("contract", "")) not in EXCLUDE_SYMBOLS
        ]
    except Exception as e:
        log.error(f"[PumpScanner] Erro ao buscar universo Gate.io: {e}")
        return []


def _filter_pump_candidates(tickers: List[dict]) -> List[dict]:
    candidates = []
    for t in tickers:
        try:
            symbol     = _from_gate(t.get("contract", ""))
            vol_usd    = float(t.get("volume_24h_quote", 0))
            pct_change = float(t.get("change_percentage", 0))  # já em %

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
                "price":          float(t.get("last", 0)),
                "price_change":   pct_change,
                "volume_24h":     vol_usd,
                "pump_potential": pump_potential,
            })
        except (ValueError, TypeError):
            continue

    candidates.sort(key=lambda x: x["pump_potential"], reverse=True)
    return candidates[:HOT_SCAN_TOP_N]


def _get_ticker(gate_symbol: str) -> dict:
    try:
        r = requests.get(
            f"{GATEIO_FUTURES}/tickers",
            params={"contract": gate_symbol},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data[0] if data else {}
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        log.debug(f"[PumpScanner] Ticker error {gate_symbol}: {e}")
        return {}


def _get_orderbook(gate_symbol: str) -> dict:
    try:
        r = requests.get(
            f"{GATEIO_FUTURES}/order_book",
            params={"contract": gate_symbol, "limit": 50, "interval": "0"},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        bids = [[b["p"], str(b["s"])] for b in data.get("bids", []) if isinstance(b, dict)]
        asks = [[a["p"], str(a["s"])] for a in data.get("asks", []) if isinstance(a, dict)]
        return {"bids": bids, "asks": asks}
    except Exception:
        return {"bids": [], "asks": []}


def _get_klines(gate_symbol: str, interval: str = "15m", limit: int = 48) -> list:
    try:
        r = requests.get(
            f"{GATEIO_FUTURES}/candlesticks",
            params={"contract": gate_symbol, "interval": interval, "limit": limit},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        lst = r.json()
        if not isinstance(lst, list):
            return []
        return [
            [str(k["t"]), str(k.get("o", 0)), str(k.get("h", 0)),
             str(k.get("l", 0)), str(k.get("c", 0)), str(k.get("v", 0))]
            for k in lst if isinstance(k, dict)
        ]
    except Exception:
        return []


def _get_recent_trades(gate_symbol: str, limit: int = 100) -> list:
    try:
        r = requests.get(
            f"{GATEIO_FUTURES}/trades",
            params={"contract": gate_symbol, "limit": limit},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        lst = r.json()
        if not isinstance(lst, list):
            return []
        return [
            {
                "p": str(t.get("price", 0)),
                "q": str(abs(t.get("size", 0))),
                "m": t.get("size", 0) < 0,
            }
            for t in lst if isinstance(t, dict)
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
