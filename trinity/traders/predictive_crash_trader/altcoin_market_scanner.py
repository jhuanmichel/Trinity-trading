"""
altcoin_market_scanner.py — Scanner Multi-Exchange de Altcoins (Cap. 19)

Escaneia o universo de altcoins em Binance Futures e retorna candidatos
pré-filtrados para análise profunda de crash.

Pipeline:
  1. Universo  — busca todos os pares USDT perpetuais (Binance Futures)
  2. Filtro    — volume > $10M/24h OU variação > ±3% OU funding extremo
  3. Hot scan  — top 50 candidatos ordenados por risco de crash preliminar
  4. Deep data — para cada hot candidate, busca orderbook + funding + OI + L/S + klines + trades

Exchanges suportadas:
  - Binance Futures (primária — endpoints públicos, sem API key)

Binance Futures endpoints:
  Base: https://fapi.binance.com
  - GET /fapi/v1/exchangeInfo              → lista de símbolos
  - GET /fapi/v1/ticker/24hr              → preço + volume + variação
  - GET /fapi/v1/depth?symbol=X&limit=50  → orderbook
  - GET /fapi/v1/fundingRate?symbol=X&limit=1  → funding rate atual
  - GET /fapi/v1/openInterest?symbol=X    → open interest
  - GET /fapi/v1/klines?symbol=X&interval=15m&limit=48  → klines 15m
  - GET /fapi/v1/aggTrades?symbol=X&limit=100  → trades recentes
  - GET /futures/data/globalLongShortAccountRatio?symbol=X&period=5m&limit=2
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BINANCE_FAPI         = "https://fapi.binance.com"
UNIVERSE_CACHE_TTL   = 300    # universo atualizado a cada 5 minutos
HOT_SCAN_TOP_N       = 50     # top 50 candidatos para deep scan
REQUEST_TIMEOUT      = 6      # timeout HTTP em segundos
MAX_WORKERS          = 10     # paralelismo no deep scan
EXCLUDE_SYMBOLS      = {      # símbolos excluídos (índices, etc.)
    "BTCUSDT", "ETHUSDT",     # BTC e ETH excluídos — foco em altcoins
    "DEFIUSDT", "ALTUSDT",
}

# Filtros mínimos para entrar no hot scan
MIN_VOLUME_24H_USD   = 10_000_000   # $10M/24h
MIN_PRICE_CHANGE_PCT = 2.5          # variação > ±2.5%
FUNDING_EXTREME_ABS  = 0.0005       # |funding| > 0.05% = extremo

_universe_cache: dict = {"data": [], "ts": 0.0}


# ── Public API ────────────────────────────────────────────────────────────────

def scan_universe() -> List[dict]:
    """
    Retorna o universo filtrado de altcoins (top 50 hot candidates).

    Usa cache de 5 minutos para o universo completo.
    """
    now = time.time()
    if _universe_cache["data"] and (now - _universe_cache["ts"]) < UNIVERSE_CACHE_TTL:
        return _universe_cache["data"]

    log.info("[Scanner] Atualizando universo Binance Futures...")
    raw = _fetch_universe()
    if not raw:
        log.warning("[Scanner] Universo vazio — retornando cache anterior")
        return _universe_cache["data"]

    hot = _filter_hot_candidates(raw)
    _universe_cache.update({"data": hot, "ts": now})
    log.info(f"[Scanner] Universo atualizado: {len(raw)} pares → {len(hot)} hot candidates")
    return hot


def fetch_coin_data(symbol: str) -> Optional[dict]:
    """
    Busca dados completos de um símbolo para análise de crash.

    Retorna dict com:
      symbol, price, price_change_pct, volume_24h, high_24h, low_24h,
      funding_rate, open_interest, oi_change_pct, long_short_ratio,
      orderbook, klines_15m, recent_trades
    """
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

        price = float(ticker.get("lastPrice", 0))

        # OI change — compara com valor anterior (aproximado via klines de OI)
        oi_usd      = float(oi.get("openInterest", 0)) * price
        oi_change   = _estimate_oi_change(symbol, oi_usd)

        return {
            "symbol":            symbol,
            "price":             price,
            "price_change_pct":  float(ticker.get("priceChangePercent", 0)),
            "volume_24h":        float(ticker.get("quoteVolume", 0)),
            "high_24h":          float(ticker.get("highPrice", price)),
            "low_24h":           float(ticker.get("lowPrice", price)),
            "funding_rate":      funding,
            "open_interest":     oi_usd,
            "oi_change_pct":     oi_change,
            "long_short_ratio":  ls_ratio,
            "orderbook":         ob,
            "klines_15m":        klines,
            "recent_trades":     trades,
        }
    except Exception as e:
        log.warning(f"[Scanner] Erro ao buscar dados de {symbol}: {e}")
        return None


def fetch_batch(symbols: List[str], max_workers: int = MAX_WORKERS) -> List[dict]:
    """
    Busca dados de múltiplos símbolos em paralelo.

    Retorna lista de coin_data dicts (sem Nones).
    """
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_coin_data, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            data = fut.result()
            if data:
                results.append(data)
    return results


# ── Internal fetch helpers ────────────────────────────────────────────────────

def _fetch_universe() -> List[dict]:
    """Busca todos os tickers de Binance Futures."""
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/ticker/24hr",
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        # Filtra apenas USDT perp, exclui bloqueados
        return [
            t for t in data
            if isinstance(t, dict)
            and t.get("symbol", "").endswith("USDT")
            and t.get("symbol") not in EXCLUDE_SYMBOLS
        ]
    except Exception as e:
        log.error(f"[Scanner] Erro ao buscar universo: {e}")
        return []


def _filter_hot_candidates(tickers: List[dict]) -> List[dict]:
    """
    Filtra e ordena os melhores candidatos para crash scan.

    Critérios de entrada (OR):
      - volume_24h > MIN_VOLUME_24H_USD
      - |price_change| > MIN_PRICE_CHANGE_PCT
      - |funding_rate| > FUNDING_EXTREME_ABS (não disponível no ticker — usa proxy)

    Score de risco preliminar para ordenação:
      risk_score = |price_change| * 2 + (volume / $100M) * 3
      Favorece queda forte + alto volume (= mais ativo = mais risco real)
    """
    candidates = []

    for t in tickers:
        try:
            symbol     = t.get("symbol", "")
            vol_usd    = float(t.get("quoteVolume", 0))
            pct_change = float(t.get("priceChangePercent", 0))

            passes_vol    = vol_usd >= MIN_VOLUME_24H_USD
            passes_change = abs(pct_change) >= MIN_PRICE_CHANGE_PCT

            if not (passes_vol or passes_change):
                continue

            # Score de risco: penaliza quedas (foco em crash)
            drop_weight  = max(0, -pct_change) * 3.0  # só quedas
            volume_score = min(10.0, vol_usd / 100_000_000)
            risk_score   = drop_weight + volume_score

            candidates.append({
                "symbol":        symbol,
                "price":         float(t.get("lastPrice", 0)),
                "price_change":  pct_change,
                "volume_24h":    vol_usd,
                "risk_score":    risk_score,
            })
        except (ValueError, TypeError):
            continue

    # Ordena por risco desc — top N
    candidates.sort(key=lambda x: x["risk_score"], reverse=True)
    return candidates[:HOT_SCAN_TOP_N]


def _get_ticker(symbol: str) -> dict:
    r = requests.get(
        f"{BINANCE_FAPI}/fapi/v1/ticker/24hr",
        params={"symbol": symbol},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def _get_funding_rate(symbol: str) -> float:
    """Retorna funding rate atual (float). Ex: 0.0001 = 0.01%"""
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": 1},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return float(data[0].get("fundingRate", 0))
        return 0.0
    except Exception:
        return 0.0


def _get_open_interest(symbol: str) -> dict:
    """Retorna open interest em contratos (convertido para USD no caller)."""
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/openInterest",
            params={"symbol": symbol},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"openInterest": 0}


def _get_long_short_ratio(symbol: str) -> float:
    """Retorna long/short ratio global de contas."""
    try:
        # Remove "USDT" para o endpoint de dados
        base = symbol.replace("USDT", "")
        r = requests.get(
            f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": "5m", "limit": 2},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return float(data[0].get("longShortRatio", 1.0))
        return 1.0
    except Exception:
        return 1.0


def _get_orderbook(symbol: str) -> dict:
    """Retorna orderbook com 50 níveis."""
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/depth",
            params={"symbol": symbol, "limit": 50},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()  # {"bids": [...], "asks": [...]}
    except Exception:
        return {"bids": [], "asks": []}


def _get_klines(symbol: str, interval: str = "15m", limit: int = 48) -> list:
    """Retorna klines OHLCV. Ex: últimas 48 velas de 15m = ~12h."""
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


def _get_recent_trades(symbol: str, limit: int = 100) -> list:
    """Retorna trades recentes (aggTrades)."""
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/aggTrades",
            params={"symbol": symbol, "limit": limit},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


# Cache simples de OI anterior para calcular variação
_oi_prev_cache: dict = {}


def _estimate_oi_change(symbol: str, current_oi_usd: float) -> float:
    """
    Estima variação de OI comparando com valor anterior em cache.
    Retorna variação percentual (pode ser 0.0 na primeira chamada).
    """
    prev = _oi_prev_cache.get(symbol, current_oi_usd)
    _oi_prev_cache[symbol] = current_oi_usd

    if prev == 0:
        return 0.0
    return round((current_oi_usd - prev) / prev * 100, 2)
