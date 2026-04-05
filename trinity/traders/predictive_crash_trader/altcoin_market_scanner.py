"""
altcoin_market_scanner.py — Scanner MEXC Futures + Enrichment (Cap. 19)

Pipeline de dados por coin:
  1. MEXC Futures /ticker      → preço, volume, funding single-exchange
  2. MEXC Futures /openInterest → OI em contratos
  3. MEXC Futures /kline        → 48 candles 15m
  4. MEXC Futures /deals        → 100 trades recentes
  5. MEXC Futures /depth        → orderbook (pode retornar 403 → vazio)
  6. CoinGlass L/S ratio        → long/short ratio real (cache 5min)
  7. CoinGlass funding multi-ex → média de funding em N exchanges (cache 5min)
  8. MEXC Spot volume           → volume spot 24h para ratio spot/perp

MEXC Futures: https://contract.mexc.com/api/v1/contract
MEXC Spot:    https://api.mexc.com/api/v3
CoinGlass v3: https://open-api-v3.coinglass.com/api
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
MEXC_CONTRACT        = "https://contract.mexc.com/api/v1/contract"
MEXC_SPOT            = "https://api.mexc.com/api/v3"
CG_BASE              = "https://open-api-v3.coinglass.com/api"

UNIVERSE_CACHE_TTL   = 300
HOT_SCAN_TOP_N       = 50
REQUEST_TIMEOUT      = 10
MAX_WORKERS          = 10
ENRICH_CACHE_TTL     = 300   # 5min — mesmo TTL do universo

EXCLUDE_SYMBOLS      = {
    "BTCUSDT", "ETHUSDT",
    "DEFIUSDT", "ALTUSDT",
}

MIN_VOLUME_24H_USD   = 10_000_000
MIN_PRICE_CHANGE_PCT = 2.5

_universe_cache: dict = {"data": [], "ts": 0.0}
_ls_cache:       dict = {}   # {symbol: {"v": float, "ts": float}}
_fund_cache:     dict = {}   # {symbol: {"v": float|None, "ts": float}}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)",
    "Accept": "application/json",
}

# Lazy-load CoinGlass API key (evita import no topo que pode falhar em alguns ambientes)
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

def _to_mexc(symbol: str) -> str:
    """BTCUSDT → BTC_USDT"""
    if symbol.endswith("USDT") and "_" not in symbol:
        return symbol[:-4] + "_USDT"
    return symbol


def _from_mexc(symbol: str) -> str:
    """BTC_USDT → BTCUSDT"""
    return symbol.replace("_", "")


def _to_cg(symbol: str) -> str:
    """SOLUSDT → SOL (CoinGlass usa coin base)"""
    return symbol.replace("USDT", "").replace("_", "")


# ── Public API ────────────────────────────────────────────────────────────────

def scan_universe() -> List[dict]:
    """Retorna top 50 hot candidates filtrados por risco de crash (cache 5min)."""
    now = time.time()
    if _universe_cache["data"] and (now - _universe_cache["ts"]) < UNIVERSE_CACHE_TTL:
        return _universe_cache["data"]

    log.info("[Scanner] Atualizando universo MEXC Futures...")
    raw = _fetch_universe()
    if not raw:
        log.warning("[Scanner] Universo vazio — retornando cache anterior")
        return _universe_cache["data"]

    hot = _filter_hot_candidates(raw)
    _universe_cache.update({"data": hot, "ts": now})
    log.info(f"[Scanner] {len(raw)} pares → {len(hot)} hot candidates")
    return hot


def fetch_coin_data(symbol: str) -> Optional[dict]:
    """Busca dados completos de um símbolo para análise de crash."""
    try:
        mexc_sym = _to_mexc(symbol)

        # ── Dados MEXC Futures ──────────────────────────────────────────────
        ticker  = _get_ticker(mexc_sym)
        oi      = _get_open_interest(mexc_sym)
        ob      = _get_orderbook(mexc_sym)
        klines  = _get_klines(mexc_sym)
        trades  = _get_recent_trades(mexc_sym)

        if not ticker:
            return None

        funding_single = float(ticker.get("fundingRate", 0))
        price          = float(ticker.get("lastPrice", 0))
        oi_usd         = float(oi) * price if oi else 0.0
        oi_change      = _estimate_oi_change(symbol, oi_usd)
        vol_futures    = float(ticker.get("turnover24", 0) or ticker.get("volume24", 0) or 0)

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
            "price_change_pct":   float(ticker.get("riseFallRate", 0)) * 100,
            "volume_24h":         vol_futures,
            "high_24h":           float(ticker.get("highPrice24", price)),
            "low_24h":            float(ticker.get("lowPrice24", price)),
            "funding_rate":       funding_final,    # multi-exchange se disponível
            "open_interest":      oi_usd,
            "oi_change_pct":      oi_change,
            "long_short_ratio":   ls_ratio,         # real (CoinGlass) ou 1.0
            "orderbook":          ob,
            "klines_15m":         klines,
            "recent_trades":      trades,
            # campos extras
            "spot_volume_24h":    spot_vol,
            "spot_futures_ratio": spot_futures,     # >3 = pump especulativo; <1 = spot lidera
            "funding_single_ex":  funding_single,   # funding só MEXC (referência)
        }
    except Exception as e:
        log.warning(f"[Scanner] Erro ao buscar dados de {symbol}: {e}")
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
    """
    Long/Short ratio via CoinGlass (cache 5min).
    Retorna 1.0 se chave ausente ou falha.
    """
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
            log.debug(f"[Scanner] CoinGlass rate limit (L/S {symbol})")
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
        log.debug(f"[Scanner] CoinGlass L/S error {symbol}: {e}")

    return 1.0


def _get_funding_multi_cg(symbol: str, key: str) -> Optional[float]:
    """
    Funding rate médio cross-exchange via CoinGlass (cache 5min).
    Retorna None se chave ausente ou falha → fallback para funding MEXC.
    """
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
            log.debug(f"[Scanner] CoinGlass rate limit (funding {symbol})")
            return _fund_cache.get(symbol, {}).get("v")
        if r.status_code != 200:
            return None

        data = r.json().get("data", [])
        if isinstance(data, list) and data:
            rates = [
                float(item.get("fundingRate", item.get("rate", 0)))
                for item in data
                if isinstance(item, dict) and (
                    "fundingRate" in item or "rate" in item
                )
            ]
            if rates:
                avg = sum(rates) / len(rates)
                _fund_cache[symbol] = {"v": avg, "ts": now}
                return avg
    except Exception as e:
        log.debug(f"[Scanner] CoinGlass funding error {symbol}: {e}")

    return None


def _get_spot_volume_mexc(symbol: str) -> float:
    """
    Volume spot 24h em USD via MEXC Spot (sem key, sempre acessível no Render).
    Usado para calcular spot_futures_ratio.
    """
    try:
        r = requests.get(
            f"{MEXC_SPOT}/ticker/24hr",
            params={"symbol": symbol},
            headers=_HEADERS,
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        return float(data.get("quoteVolume", 0))
    except Exception:
        return 0.0


# ── MEXC Futures helpers ──────────────────────────────────────────────────────

def _fetch_universe() -> List[dict]:
    try:
        r = requests.get(
            f"{MEXC_CONTRACT}/ticker",
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        raw     = r.json()
        tickers = raw.get("data", raw) if isinstance(raw, dict) else raw
        if not isinstance(tickers, list):
            log.error(f"[Scanner] Formato inesperado do ticker MEXC: {type(tickers)}")
            return []
        return [
            t for t in tickers
            if isinstance(t, dict)
            and t.get("symbol", "").endswith("_USDT")
            and _from_mexc(t.get("symbol", "")) not in EXCLUDE_SYMBOLS
        ]
    except Exception as e:
        log.error(f"[Scanner] Erro ao buscar universo MEXC: {e}")
        return []


def _filter_hot_candidates(tickers: List[dict]) -> List[dict]:
    candidates = []
    for t in tickers:
        try:
            symbol     = _from_mexc(t.get("symbol", ""))
            vol_usd    = float(t.get("turnover24", 0) or t.get("volume24", 0) or 0)
            pct_change = float(t.get("riseFallRate", 0)) * 100

            if not (vol_usd >= MIN_VOLUME_24H_USD or abs(pct_change) >= MIN_PRICE_CHANGE_PCT):
                continue

            drop_weight  = max(0, -pct_change) * 3.0
            volume_score = min(10.0, vol_usd / 100_000_000)
            risk_score   = drop_weight + volume_score

            candidates.append({
                "symbol":       symbol,
                "price":        float(t.get("lastPrice", 0)),
                "price_change": pct_change,
                "volume_24h":   vol_usd,
                "risk_score":   risk_score,
            })
        except (ValueError, TypeError):
            continue

    candidates.sort(key=lambda x: x["risk_score"], reverse=True)
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
        log.debug(f"[Scanner] Ticker error {mexc_symbol}: {e}")
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
                "m": t.get("T", t.get("side", 2)) == 2,
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
