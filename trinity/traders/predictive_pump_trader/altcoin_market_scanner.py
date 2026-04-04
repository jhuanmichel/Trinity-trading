"""
altcoin_market_scanner.py — Scanner de Pump Candidates (Cap. 20)

Escaneia o universo de altcoins em Binance Futures filtrando candidatos
com maior probabilidade de pump.

Filtros de pump candidates (diferente do crash scanner):
  - Volume > $10M/24h (liquidez mínima)
  - Price change > -5% E < +3% (caiu mas ainda não explodiu)
  - Funding negativo (< -0.02%) = shorts pagando = squeeze fuel
  - OR: volume spike (> 150% da média) = acumulação acontecendo

Hot scan: top 50 candidatos ordenados por "pump potential score":
  pump_potential = (-price_change * 2) + (volume_ratio * 3) + (squeeze_potential * 5)
  Favorece: queda recente + alto volume + muitos shorts = ripe for squeeze

Reutiliza os mesmos endpoints da Binance Futures do crash scanner.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BINANCE_FAPI         = "https://fapi.binance.com"
UNIVERSE_CACHE_TTL   = 300    # cache de 5 minutos
HOT_SCAN_TOP_N       = 50     # top 50 para deep scan
REQUEST_TIMEOUT      = 6
MAX_WORKERS          = 10
EXCLUDE_SYMBOLS      = {
    "BTCUSDT", "ETHUSDT",
    "DEFIUSDT", "ALTUSDT",
}

# Filtros para pump candidates
MIN_VOLUME_24H_USD   = 10_000_000  # $10M
MAX_PRICE_CHANGE_PCT = 3.0         # não entrou em pump ainda
MIN_PRICE_DROP_PCT   = -5.0        # caiu mas não colapsou
FUNDING_SQUEEZE_THRESH = -0.0002   # funding < -0.02% = shorts sobrecarregados

_universe_cache: dict = {"data": [], "ts": 0.0}


# ── Public API ────────────────────────────────────────────────────────────────

def scan_universe() -> List[dict]:
    """Retorna top 50 pump candidates filtrados (cache 5min)."""
    now = time.time()
    if _universe_cache["data"] and (now - _universe_cache["ts"]) < UNIVERSE_CACHE_TTL:
        return _universe_cache["data"]

    log.info("[PumpScanner] Atualizando universo de pump candidates...")
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
        oi_usd    = float(oi.get("openInterest", 0)) * price
        oi_change = _estimate_oi_change(symbol, oi_usd)

        return {
            "symbol":           symbol,
            "price":            price,
            "price_change_pct": float(ticker.get("priceChangePercent", 0)),
            "volume_24h":       float(ticker.get("quoteVolume", 0)),
            "high_24h":         float(ticker.get("highPrice", price)),
            "low_24h":          float(ticker.get("lowPrice", price)),
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
            f"{BINANCE_FAPI}/fapi/v1/ticker/24hr",
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return [
            t for t in data
            if isinstance(t, dict)
            and t.get("symbol", "").endswith("USDT")
            and t.get("symbol") not in EXCLUDE_SYMBOLS
        ]
    except Exception as e:
        log.error(f"[PumpScanner] Erro ao buscar universo: {e}")
        return []


def _filter_pump_candidates(tickers: List[dict]) -> List[dict]:
    """
    Filtra candidatos a pump e ordena por pump potential.

    Candidato ideal:
      - Caiu recentemente mas não colapsou (zona de acumulação)
      - Alto volume (institucional entrando)
      - Pode ter funding negativo (shorts sobrecarregados)

    pump_potential = (queda_weight) + (volume_score) + (squeeze_bonus)
    """
    candidates = []

    for t in tickers:
        try:
            symbol     = t.get("symbol", "")
            vol_usd    = float(t.get("quoteVolume", 0))
            pct_change = float(t.get("priceChangePercent", 0))

            # Filtro de volume mínimo
            if vol_usd < MIN_VOLUME_24H_USD:
                continue

            # Filtro de preço: não entrou em pump ainda
            if pct_change > MAX_PRICE_CHANGE_PCT:
                continue

            # Filtro: não colapsou demais
            if pct_change < -20.0:
                continue

            # Pump potential score
            # Premia quedas moderadas (zona de compra)
            drop_score   = max(0, -pct_change) * 2.0  # 0 = flat, 10 = -5%
            volume_score = min(10.0, vol_usd / 100_000_000)

            pump_potential = drop_score + volume_score

            candidates.append({
                "symbol":        symbol,
                "price":         float(t.get("lastPrice", 0)),
                "price_change":  pct_change,
                "volume_24h":    vol_usd,
                "pump_potential": pump_potential,
            })
        except (ValueError, TypeError):
            continue

    candidates.sort(key=lambda x: x["pump_potential"], reverse=True)
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
    try:
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
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/depth",
            params={"symbol": symbol, "limit": 50},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"bids": [], "asks": []}


def _get_klines(symbol: str, interval: str = "15m", limit: int = 48) -> list:
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


_oi_prev_cache: dict = {}


def _estimate_oi_change(symbol: str, current_oi_usd: float) -> float:
    prev = _oi_prev_cache.get(symbol, current_oi_usd)
    _oi_prev_cache[symbol] = current_oi_usd
    if prev == 0:
        return 0.0
    return round((current_oi_usd - prev) / prev * 100, 2)
