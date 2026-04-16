"""
okx_adapter.py — Adapter OKX Futures (SWAP / USDT-M)

Campos confirmados via probe (2026-04-16), 309 contratos:
  instId    : "BTC-USDT-SWAP"   (formato: BASE-QUOTE-SWAP)
  last      : str               preço atual
  bidPx     : str               melhor bid
  askPx     : str               melhor ask
  volCcy24h : str               volume 24h em base currency (≈ multiplica pelo preço → USD)
  open24h   : str               preço de abertura 24h atrás (para calcular change_24h)
  — fundingRate NÃO disponível no ticker batch

Funding rate via endpoint individual (probe 2026-04-16):
  GET /api/v5/public/funding-rate?instId=BTC-USDT-SWAP
  Campo: data[0]["fundingRate"] (string decimal)
  Sem endpoint batch público (erro 50014 ao omitir instId)
  Solução: ThreadPoolExecutor top 100 por volume, cache 5 minutos.

Endpoint público — sem API key.
"""

import logging
import threading
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from .base_adapter import ExchangeAdapter, NormalizedTicker

logger = logging.getLogger(__name__)

BASE_URL = "https://www.okx.com/api/v5"
TIMEOUT  = 15
UA       = "Trinity/5.0"

KLINE_INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "1H", "4h": "4H", "1d": "1D",
}

# ── Funding rate batch cache (módulo-level, compartilhado entre instâncias) ───

_okx_funding_cache:     dict  = {}     # {inst_id: funding_rate_float}
_okx_funding_cache_ts:  float = 0.0
_okx_funding_cache_lock       = threading.Lock()

OKX_FUNDING_CACHE_TTL = 300   # 5 minutos — funding muda a cada 8h
OKX_FUNDING_TOP_N     = 100   # buscar funding dos top N por volume
OKX_FUNDING_WORKERS   = 10    # requests paralelos simultâneos
OKX_FUNDING_DELAY     = 0.05  # 50ms entre batches de workers (≈ 20 req/s)


class OkxAdapter(ExchangeAdapter):

    def __init__(self):
        self._cache: list[NormalizedTicker] = []
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 5.0

    @property
    def name(self) -> str:
        return "okx"

    # ── Funding rate individual ───────────────────────────────────────────────

    def _fetch_single_funding(self, inst_id: str) -> tuple[str, float]:
        """Busca funding rate de um único instrumento OKX. Fail-silent."""
        try:
            r = requests.get(
                f"{BASE_URL}/public/funding-rate",
                params={"instId": inst_id},
                timeout=10,
                headers={"User-Agent": UA},
            )
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    return (inst_id, self._safe_float(data[0].get("fundingRate", 0)))
        except Exception:
            pass
        return (inst_id, 0.0)

    # ── Funding rate batch (top N por volume, paralelo, cache 5min) ───────────

    def _fetch_funding_batch(self, symbols_raw: list[str]) -> dict[str, float]:
        """
        Busca funding rates dos top N contratos OKX em paralelo.
        Cache de 5 minutos — funding muda a cada 8h, refresh frequente é desperdício.
        """
        global _okx_funding_cache, _okx_funding_cache_ts

        now = time.time()
        with _okx_funding_cache_lock:
            if now - _okx_funding_cache_ts < OKX_FUNDING_CACHE_TTL and _okx_funding_cache:
                return _okx_funding_cache.copy()

        top = symbols_raw[:OKX_FUNDING_TOP_N]
        results: dict[str, float] = {}

        with ThreadPoolExecutor(max_workers=OKX_FUNDING_WORKERS) as executor:
            futures = {}
            for i, sym in enumerate(top):
                futures[executor.submit(self._fetch_single_funding, sym)] = sym
                # Pausa entre batches de workers para não estourar rate limit
                if i > 0 and i % OKX_FUNDING_WORKERS == 0:
                    time.sleep(OKX_FUNDING_DELAY * OKX_FUNDING_WORKERS)

            for future in as_completed(futures, timeout=60):
                try:
                    inst_id, fr = future.result()
                    results[inst_id] = fr
                except Exception:
                    pass

        with _okx_funding_cache_lock:
            _okx_funding_cache    = results
            _okx_funding_cache_ts = time.time()

        real = sum(1 for v in results.values() if v != 0.0)
        logger.info(
            f"[OKX] Funding batch: {len(results)} contratos buscados, "
            f"{real} com funding real"
        )
        return results

    # ── Tickers ───────────────────────────────────────────────────────────────

    def fetch_all_tickers(self) -> list[NormalizedTicker]:
        now = time.time()
        if now - self._cache_ts < self._cache_ttl and self._cache:
            return self._cache

        try:
            r = requests.get(
                f"{BASE_URL}/market/tickers",
                params={"instType": "SWAP"},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            raw_list = r.json().get("data", [])
        except Exception as e:
            logger.error(f"[OKX] fetch_all_tickers erro: {e}")
            return self._cache

        result = []
        for t in raw_list:
            try:
                inst_id = t.get("instId", "")
                # Filtrar: só USDT-margined SWAP
                if not inst_id.endswith("-SWAP") or "-USDT-" not in inst_id:
                    continue

                price = self._safe_float(t.get("last"))
                if price <= 0:
                    continue

                # Volume USD: volCcy24h (volume em moeda base) * preço
                vol_ccy = self._safe_float(t.get("volCcy24h"))
                vol_usd = vol_ccy * price

                # Change 24h calculado a partir de open24h
                open24 = self._safe_float(t.get("open24h"))
                chg = ((price - open24) / open24 * 100) if open24 > 0 else 0.0

                bid = self._safe_float(t.get("bidPx"))
                ask = self._safe_float(t.get("askPx"))

                # Normalizar símbolo: "BTC-USDT-SWAP" → "BTCUSDT"
                sym = self.normalize_symbol(inst_id)

                result.append(NormalizedTicker(
                    exchange            = "okx",
                    symbol              = sym,
                    symbol_raw          = inst_id,
                    last_price          = price,
                    funding_rate        = 0.0,   # será injetado abaixo
                    funding_rate_annual = 0.0,
                    open_interest_usd   = 0.0,   # sem batch endpoint OI
                    volume_24h_usd      = vol_usd,
                    change_24h_pct      = chg,
                    bid_price           = bid if bid > 0 else None,
                    ask_price           = ask if ask > 0 else None,
                    spread_pct          = self._calc_spread(bid, ask),
                ))
            except Exception as e:
                logger.debug(f"[OKX] parse erro ticker {t.get('instId','?')}: {e}")

        # ── Injetar funding rates reais dos top contratos por volume ──────────
        if result:
            result.sort(key=lambda t: t.volume_24h_usd, reverse=True)
            top_symbols_raw = [t.symbol_raw for t in result[:OKX_FUNDING_TOP_N]]
            funding_map = self._fetch_funding_batch(top_symbols_raw)

            for ticker in result:
                fr = funding_map.get(ticker.symbol_raw, 0.0)
                if fr != 0.0:
                    ticker.funding_rate        = fr
                    ticker.funding_rate_annual = self._calc_annual(fr)

        self._cache    = result
        self._cache_ts = now
        logger.info(f"[OKX] {len(result)} tickers carregados")
        return result

    def fetch_funding_rates(self) -> dict[str, float]:
        """Retorna funding rates dos top contratos (via cache do batch)."""
        tickers = self.fetch_all_tickers()
        return {t.symbol: t.funding_rate for t in tickers if t.funding_rate != 0.0}

    def fetch_orderbook(self, symbol_raw: str, depth: int = 20) -> dict:
        try:
            r = requests.get(
                f"{BASE_URL}/market/books",
                params={"instId": symbol_raw, "sz": depth},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            data = r.json().get("data", [{}])[0]
            # OKX: {"bids": [["price", "qty", "", ""], ...], "asks": [...]}
            bids = [[self._safe_float(b[0]), self._safe_float(b[1])] for b in data.get("bids", [])]
            asks = [[self._safe_float(a[0]), self._safe_float(a[1])] for a in data.get("asks", [])]
            return {"bids": bids, "asks": asks}
        except Exception as e:
            logger.error(f"[OKX] fetch_orderbook {symbol_raw} erro: {e}")
            return {"bids": [], "asks": []}

    def fetch_recent_trades(self, symbol_raw: str, limit: int = 100) -> list[dict]:
        try:
            r = requests.get(
                f"{BASE_URL}/market/trades",
                params={"instId": symbol_raw, "limit": min(limit, 500)},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            trades = r.json().get("data", [])
            # OKX: [{"px": price, "sz": qty, "side": "buy"|"sell", "ts": str}, ...]
            return [
                {
                    "price":     self._safe_float(tr.get("px")),
                    "qty":       self._safe_float(tr.get("sz")),
                    "is_buy":    tr.get("side") == "buy",
                    "timestamp": int(self._safe_float(tr.get("ts", 0))),
                }
                for tr in trades
            ]
        except Exception as e:
            logger.error(f"[OKX] fetch_recent_trades {symbol_raw} erro: {e}")
            return []

    def fetch_klines(self, symbol_raw: str, interval: str = "15m", limit: int = 50) -> list[dict]:
        okx_interval = KLINE_INTERVALS.get(interval, "15m")
        try:
            r = requests.get(
                f"{BASE_URL}/market/candles",
                params={"instId": symbol_raw, "bar": okx_interval, "limit": limit},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            raw = r.json().get("data", [])
            # OKX: [[ts, open, high, low, close, vol, volCcy], ...]
            return [
                {
                    "timestamp": int(self._safe_float(k[0])),
                    "open":      self._safe_float(k[1]),
                    "high":      self._safe_float(k[2]),
                    "low":       self._safe_float(k[3]),
                    "close":     self._safe_float(k[4]),
                    "volume":    self._safe_float(k[5]),
                }
                for k in raw
            ]
        except Exception as e:
            logger.error(f"[OKX] fetch_klines {symbol_raw} erro: {e}")
            return []
