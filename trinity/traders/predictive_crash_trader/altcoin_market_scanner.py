"""
altcoin_market_scanner.py — Scanner MEXC Futures (Cap. 19)

Migrado: Gate.io Futures → MEXC Futures
MEXC: mesma exchange que o FMS usa com sucesso (838 contratos, ciclo 2.9s).

MEXC Futures endpoints (públicos, sem API key):
  Base: https://contract.mexc.com/api/v1/contract
  - GET /ticker              → todos os tickers USDT
  - GET /ticker?symbol=SOL_USDT  → ticker único
  - GET /kline/{symbol}?interval=Min15&limit=48
  - GET /deals/{symbol}?limit=100
  - GET /depth/{symbol}      → orderbook

Campos MEXC Futures:
  symbol:        "SOL_USDT"
  lastPrice:     preço float
  volume24:      volume 24h em contratos
  amount24:      volume 24h em USDT
  riseFallRate:  variação % em decimal (×100 para %)
  holdVol:       OI em contratos
  fundingRate:   funding atual float
  Klines:        dict com arrays {time, open, high, low, close, vol}
  Deals:         lista [{p, v, T, O, M, t}] — sign(v) indica direção

Diferenças do pump scanner:
  - Sem MAX_PRICE_CHANGE_PCT (aceita moedas +30%, +50% — já em topo)
  - MIN_PRICE_CHANGE_PCT = 2.5 (altera candidatos: moedas que já subiram = risco crash)
  - _filter_hot_candidates() em vez de _filter_pump_candidates()
  - Log prefix [CrashScanner]
"""
import logging
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
MEXC_FUTURES         = "https://contract.mexc.com/api/v1/contract"
MEXC_SPOT            = "https://api.mexc.com/api/v3"
CG_BASE              = "https://open-api-v3.coinglass.com/api"
BINANCE_FAPI         = "https://fapi.binance.com"

UNIVERSE_CACHE_TTL   = 300
HOT_SCAN_TOP_N       = 200           # varredura completa (mesmo escopo do FMS)
REQUEST_TIMEOUT      = 10
MAX_WORKERS          = 15
ENRICH_CACHE_TTL     = 300

# Filtros de símbolos a excluir — sintéticos, ações, commodities
EXCLUDED_KEYWORDS = {
    "BTCUSDT", "ETHUSDT",
    "DEFIUSDT", "ALTUSDT",
    # Sintéticos de ações / commodities / índices (copiado do FMS)
    "AAPL", "TSLA", "AMZN", "GOOG", "MSFT", "NVDA", "META", "NFLX",
    "BABA", "TSM", "AMD", "INTC", "CRM", "ORCL", "PYPL", "COIN",
    "MSTR", "MARA", "RIOT", "GME", "AMC",
    "GOLD", "SILVER", "COPPER", "OIL", "GAS", "WHEAT", "CORN",
    "SPX", "NDX", "DXY", "VIX",
    "BULL", "BEAR", "UP", "DOWN", "10S", "1000",
}

MIN_VOLUME_24H_USD   = 300_000      # $300K mínimo (mesmo do FMS)
MIN_PRICE_CHANGE_PCT = 2.5          # moedas que subiram > 2.5% = candidatas a crash

_universe_cache: dict = {"data": [], "ts": 0.0}
_ls_cache:       dict = {}
_fund_cache:     dict = {}

# OI history por símbolo — deque(maxlen=20) com (ts, oi_usd)
_oi_history: Dict[str, deque] = {}

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

def _to_mexc(symbol: str) -> str:
    """SOLUSDT → SOL_USDT"""
    if symbol.endswith("USDT") and "_" not in symbol:
        return symbol[:-4] + "_USDT"
    return symbol


def _from_mexc(symbol: str) -> str:
    """SOL_USDT → SOLUSDT"""
    return symbol.replace("_", "")


def _to_cg(symbol: str) -> str:
    """SOLUSDT → SOL"""
    return symbol.replace("USDT", "").replace("_", "")


def _is_synthetic(symbol: str) -> bool:
    """Filtra sintéticos de ações/commodities como o FMS faz."""
    upper = symbol.upper()
    for kw in EXCLUDED_KEYWORDS:
        if kw in upper:
            return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────

def scan_universe() -> List[dict]:
    """Retorna top 50 hot candidates por risco de crash (cache 5min)."""
    now = time.time()
    if _universe_cache["data"] and (now - _universe_cache["ts"]) < UNIVERSE_CACHE_TTL:
        return _universe_cache["data"]

    log.info("[CrashScanner] Atualizando universo MEXC Futures...")
    raw = _fetch_universe()
    if not raw:
        log.warning("[CrashScanner] Universo vazio — retornando cache anterior")
        return _universe_cache["data"]

    hot = _filter_hot_candidates(raw)
    _universe_cache.update({"data": hot, "ts": now})
    log.info(f"[CrashScanner] {len(raw)} pares → {len(hot)} hot candidates")
    return hot


def fetch_coin_data(symbol: str) -> Optional[dict]:
    """Busca dados completos de um símbolo para análise de crash."""
    try:
        mexc_sym = _to_mexc(symbol)

        ticker = _get_ticker(mexc_sym)
        if not ticker:
            return None

        price          = float(ticker.get("lastPrice", 0))
        oi_contracts   = float(ticker.get("holdVol", 0))
        oi_usd         = oi_contracts * price
        oi_change      = _estimate_oi_change(symbol, oi_usd)
        vol_futures    = float(ticker.get("amount24", 0))     # volume em USDT
        funding_single = float(ticker.get("fundingRate", 0))

        ob     = _get_orderbook(mexc_sym)
        klines = _get_klines(mexc_sym)
        trades = _get_recent_trades(mexc_sym)

        # ── high/low 24h a partir de klines (MEXC ticker pode não ter) ───
        high_24h, low_24h = _calc_high_low_from_klines(klines, price)

        # ── Enriquecimento multi-fonte ──────────────────────────────────────
        ls_ratio   = _get_ls_ratio_binance(symbol)
        key        = _cg_key()
        fund_multi = _get_funding_multi_cg(symbol, key)
        spot_vol   = _get_spot_volume_mexc(symbol)

        funding_final = fund_multi if fund_multi is not None else funding_single
        spot_futures  = round(vol_futures / spot_vol, 2) if spot_vol > 0 else 1.0

        # Pseudo L/S ratio via trades quando CoinGlass indisponível
        if ls_ratio == 1.0 and trades:
            ls_ratio = _derive_ls_from_trades(trades)

        # pct_change: riseFallRate é decimal (0.05 = +5%)
        pct_change = float(ticker.get("riseFallRate", 0)) * 100.0

        return {
            "symbol":             symbol,
            "price":              price,
            "price_change_pct":   pct_change,
            "volume_24h":         vol_futures,
            "high_24h":           high_24h,
            "low_24h":            low_24h,
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
        log.warning(f"[CrashScanner] Erro ao buscar dados de {symbol}: {e}")
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

def _get_ls_ratio_binance(symbol: str) -> float:
    """
    L/S ratio via Binance Futures público (sem API key).
    Endpoint: /futures/data/globalLongShortAccountRatio
    Retorna ratio long/short. Ex: 0.55 long, 0.45 short → ratio = 1.22
    Fallback: 1.0 (neutro) — coberto por _derive_ls_from_trades
    """
    now = time.time()
    cached = _ls_cache.get(symbol)
    if cached and (now - cached["ts"]) < ENRICH_CACHE_TTL:
        return cached["v"]

    # Binance usa "BTCUSDT" sem underscore
    binance_sym = symbol.replace("_", "") if "_" in symbol else symbol
    if not binance_sym.endswith("USDT"):
        binance_sym = binance_sym + "USDT"

    try:
        r = requests.get(
            f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": binance_sym, "period": "5m", "limit": 1},
            headers=_HEADERS,
            timeout=8,
        )
        if r.status_code == 429:
            return _ls_cache.get(symbol, {}).get("v", 1.0)
        if r.status_code != 200:
            return 1.0
        data = r.json()
        if isinstance(data, list) and data:
            item      = data[0]
            long_pct  = float(item.get("longAccount", 0.5))
            short_pct = float(item.get("shortAccount", 0.5))
            if short_pct > 0:
                ratio = round(long_pct / short_pct, 4)
                _ls_cache[symbol] = {"v": ratio, "ts": now}
                return ratio
    except Exception as e:
        log.debug(f"[CrashScanner] Binance L/S error {symbol}: {e}")
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
        log.debug(f"[CrashScanner] CoinGlass funding error {symbol}: {e}")
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


# ── MEXC Futures helpers ──────────────────────────────────────────────────────

def _fetch_universe() -> List[dict]:
    """Busca todos os tickers MEXC Futures USDT."""
    try:
        r = requests.get(
            f"{MEXC_FUTURES}/ticker",
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        tickers = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(tickers, list):
            log.error(f"[CrashScanner] Formato inesperado MEXC: {type(tickers)}")
            return []
        return [
            t for t in tickers
            if isinstance(t, dict)
            and str(t.get("symbol", "")).endswith("_USDT")
            and not _is_synthetic(_from_mexc(str(t.get("symbol", ""))))
            and _from_mexc(str(t.get("symbol", ""))) not in EXCLUDED_KEYWORDS
        ]
    except Exception as e:
        log.error(f"[CrashScanner] Erro ao buscar universo MEXC: {e}")
        return []


def _filter_hot_candidates(tickers: List[dict]) -> List[dict]:
    """
    Filtra candidatos a crash: moedas que já subiram (overextended) ou
    têm volume alto com queda (reversão).
    Sem MAX_PRICE_CHANGE_PCT — moedas +30%, +50% são candidatas.
    """
    candidates = []
    for t in tickers:
        try:
            symbol     = _from_mexc(str(t.get("symbol", "")))
            vol_usd    = float(t.get("amount24", 0))
            pct_change = float(t.get("riseFallRate", 0)) * 100.0

            # Inclui se: volume alto OU variação significativa (sobe ou cai)
            if not (vol_usd >= MIN_VOLUME_24H_USD or abs(pct_change) >= MIN_PRICE_CHANGE_PCT):
                continue

            # Crash scoring: moedas que subiram muito são mais perigosas
            overextension_weight = max(0, pct_change - 15.0) * 2.0   # +15%+ = crash candidate
            drop_weight          = max(0, -pct_change) * 3.0         # queda = risco reversal
            volume_score         = min(10.0, vol_usd / 10_000_000)
            risk_score           = max(drop_weight, overextension_weight) + volume_score

            candidates.append({
                "symbol":     symbol,
                "price":      float(t.get("lastPrice", 0)),
                "price_change": pct_change,
                "volume_24h": vol_usd,
                "risk_score": risk_score,
            })
        except (ValueError, TypeError):
            continue

    candidates.sort(key=lambda x: x["risk_score"], reverse=True)
    return candidates[:HOT_SCAN_TOP_N]


def _get_ticker(mexc_symbol: str) -> dict:
    """Busca ticker de um símbolo específico."""
    try:
        r = requests.get(
            f"{MEXC_FUTURES}/ticker",
            params={"symbol": mexc_symbol},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(data, list):
            return data[0] if data else {}
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        log.debug(f"[CrashScanner] Ticker error {mexc_symbol}: {e}")
        return {}


def _get_orderbook(mexc_symbol: str) -> dict:
    """
    Busca orderbook MEXC Futures.
    MEXC depth: {"asks": [{price, vol}, ...], "bids": [...]}
    ou listas [[price, vol], ...]
    """
    try:
        r = requests.get(
            f"{MEXC_FUTURES}/depth/{mexc_symbol}",
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else payload

        def normalize_side(side_list):
            result = []
            for entry in side_list:
                try:
                    if isinstance(entry, dict):
                        p = entry.get("price", entry.get("p", 0))
                        q = entry.get("vol",   entry.get("v", entry.get("q", 0)))
                    elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                        p, q = entry[0], entry[1]
                    else:
                        continue
                    result.append([str(p), str(q)])
                except (TypeError, ValueError):
                    continue
            return result

        bids = normalize_side(data.get("bids", []))
        asks = normalize_side(data.get("asks", []))
        return {"bids": bids, "asks": asks}
    except Exception:
        return {"bids": [], "asks": []}


def _get_klines(mexc_symbol: str, interval: str = "Min15", limit: int = 48) -> list:
    """
    Busca klines MEXC Futures.
    MEXC retorna dict com arrays paralelos: {time:[...], open:[...], high:[...], ...}
    Normaliza para formato [[ts, o, h, l, c, v], ...]
    """
    try:
        r = requests.get(
            f"{MEXC_FUTURES}/kline/{mexc_symbol}",
            params={"interval": interval, "limit": limit},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else payload

        if isinstance(data, dict):
            times  = data.get("time",  [])
            opens  = data.get("open",  [])
            highs  = data.get("high",  [])
            lows   = data.get("low",   [])
            closes = data.get("close", [])
            vols   = data.get("vol",   data.get("volume", []))
            n = min(len(times), len(opens), len(highs), len(lows), len(closes), len(vols))
            return [
                [str(times[i]), str(opens[i]), str(highs[i]),
                 str(lows[i]),  str(closes[i]), str(vols[i])]
                for i in range(n)
            ]

        if isinstance(data, list):
            result = []
            for k in data:
                if isinstance(k, dict):
                    result.append([
                        str(k.get("t", k.get("time", 0))),
                        str(k.get("o", k.get("open",  0))),
                        str(k.get("h", k.get("high",  0))),
                        str(k.get("l", k.get("low",   0))),
                        str(k.get("c", k.get("close", 0))),
                        str(k.get("v", k.get("vol",   0))),
                    ])
                elif isinstance(k, (list, tuple)) and len(k) >= 6:
                    result.append([str(x) for x in k[:6]])
            return result

        return []
    except Exception as e:
        log.debug(f"[CrashScanner] Klines error {mexc_symbol}: {e}")
        return []


def _get_recent_trades(mexc_symbol: str, limit: int = 100) -> list:
    """
    Busca trades recentes MEXC Futures.
    MEXC deals: [{p: preço, v: qty (com sinal), T: ts, ...}]
    v positivo = buy, v negativo = sell.
    Normaliza para formato {"p", "q", "m"} compatível com detectores.
    """
    try:
        r = requests.get(
            f"{MEXC_FUTURES}/deals/{mexc_symbol}",
            params={"limit": limit},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else payload

        if isinstance(data, dict):
            data = data.get("resultList", data.get("list", []))

        if not isinstance(data, list):
            return []

        result = []
        for t in data:
            if not isinstance(t, dict):
                continue
            try:
                price_val = float(t.get("p", t.get("price", 0)))
                # MEXC: v é sempre positivo — direção fica em T (1=buy, 2=sell)
                size_raw  = t.get("v", t.get("size", t.get("vol", 0)))
                qty       = abs(float(size_raw))

                # T=1 → taker buy (is_sell=False), T=2 → taker sell (is_sell=True)
                t_field = t.get("T")
                if t_field is not None:
                    is_sell = (int(t_field) == 2)
                else:
                    # Fallback: sign de v caso seja negativo (outras versões da API)
                    is_sell = float(size_raw) < 0

                result.append({
                    "p": str(price_val),
                    "q": str(qty),
                    "m": is_sell,
                })
            except (ValueError, TypeError):
                continue

        return result
    except Exception as e:
        log.debug(f"[CrashScanner] Trades error {mexc_symbol}: {e}")
        return []


def _calc_high_low_from_klines(klines: list, price: float) -> tuple:
    """
    Calcula high/low 24h a partir das últimas 96 velas 15m (= 24h).
    Fallback para price ±5% se klines vazio.
    """
    if not klines:
        return price * 1.05, price * 0.95

    high = 0.0
    low  = float("inf")
    for k in klines[-96:]:
        try:
            h = float(k[2])
            l = float(k[3])
            if h > high:
                high = h
            if l < low:
                low = l
        except (ValueError, TypeError, IndexError):
            continue

    if high == 0.0 or low == float("inf"):
        return price * 1.05, price * 0.95

    return high, low


def _estimate_oi_change(symbol: str, current_oi_usd: float) -> float:
    """
    Calcula variação de OI vs baseline de ~5min atrás.
    Usa deque(maxlen=20) — cada scan é ~15s, 20 entradas ≈ 5min.
    """
    now = time.time()
    if symbol not in _oi_history:
        _oi_history[symbol] = deque(maxlen=20)

    hist = _oi_history[symbol]
    hist.append((now, current_oi_usd))

    if len(hist) < 2:
        return 0.0

    oldest_ts, oldest_oi = hist[0]
    if oldest_oi == 0:
        return 0.0

    return round((current_oi_usd - oldest_oi) / oldest_oi * 100, 2)


def _derive_ls_from_trades(trades: list) -> float:
    """
    Deriva pseudo L/S ratio a partir do buy/sell volume dos trades recentes.
    Só usado quando CoinGlass retorna fallback 1.0.

    buy_vol / sell_vol ≈ proxy de long/short ratio.
    Exemplos:
      70% sell, 30% buy → 0.43 → short_heavy (squeeze fuel)
      50/50             → 1.00 → neutro
      30% sell, 70% buy → 2.33 → long_heavy
    """
    buy_vol  = 0.0
    sell_vol = 0.0

    for t in trades:
        try:
            qty   = float(t.get("q", 0))
            price = float(t.get("p", 0))
            usd   = qty * price
            if t.get("m", False):   # m=True → sell
                sell_vol += usd
            else:
                buy_vol  += usd
        except (ValueError, TypeError):
            continue

    if sell_vol <= 0:
        return 2.0  # sem sells → longs dominam totalmente

    ratio = buy_vol / sell_vol
    # Clamp entre 0.2 e 5.0 para evitar extremos por amostra pequena
    return round(max(0.2, min(5.0, ratio)), 4)
