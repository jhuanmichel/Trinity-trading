"""
binance_adapter.py — Adapter Binance Futures (USDT-M)

Campos confirmados via probe (2026-04-16), 709 contratos:
  symbol             : "BTCUSDT"   (sem separador, só perpétuos)
  lastPrice          : str         preço atual
  quoteVolume        : str         volume 24h em USDT
  priceChangePercent : str         variação 24h já em % (ex: "1.459")
  — funding rate NÃO está no ticker 24hr

Funding rate: GET /fapi/v1/premiumIndex (sem symbol = todos)
  campo: "lastFundingRate" (str, decimal)

OI: não disponível em batch neste sprint (0.0).

Endpoints públicos — sem API key.
"""

import logging
import time
import requests

from .base_adapter import ExchangeAdapter, NormalizedTicker

logger = logging.getLogger(__name__)

BASE_URL = "https://fapi.binance.com/fapi/v1"
TIMEOUT  = 15
UA       = "Trinity/5.0"

KLINE_INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "1h", "4h": "4h", "1d": "1d",
}


class BinanceAdapter(ExchangeAdapter):

    def __init__(self):
        self._cache: list[NormalizedTicker] = []
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 5.0

    @property
    def name(self) -> str:
        return "binance"

    def _fetch_funding_map(self) -> dict[str, float]:
        """Busca funding rates de todos os instrumentos em 1 request."""
        try:
            r = requests.get(
                f"{BASE_URL}/premiumIndex",
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            items = r.json()
            if not isinstance(items, list):
                return {}
            return {
                item["symbol"]: self._safe_float(item.get("lastFundingRate"))
                for item in items
                if "symbol" in item
            }
        except Exception as e:
            logger.error(f"[BINANCE] _fetch_funding_map erro: {e}")
            return {}

    def fetch_all_tickers(self) -> list[NormalizedTicker]:
        now = time.time()
        if now - self._cache_ts < self._cache_ttl and self._cache:
            return self._cache

        # 2 requests: tickers + funding rates
        try:
            r = requests.get(
                f"{BASE_URL}/ticker/24hr",
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            raw_list = r.json()
        except Exception as e:
            logger.error(f"[BINANCE] fetch_all_tickers erro: {e}")
            return self._cache

        funding_map = self._fetch_funding_map()

        result = []
        for t in raw_list:
            try:
                sym_raw = t.get("symbol", "")
                # Filtrar: só perpétuos USDT (sem underscore/data de vencimento)
                if "_" in sym_raw or not sym_raw.endswith("USDT"):
                    continue

                price    = self._safe_float(t.get("lastPrice"))
                if price <= 0:
                    continue

                funding  = funding_map.get(sym_raw, 0.0)
                vol_usd  = self._safe_float(t.get("quoteVolume"))
                chg      = self._safe_float(t.get("priceChangePercent"))  # já em %

                result.append(NormalizedTicker(
                    exchange            = "binance",
                    symbol              = sym_raw,          # já normalizado: BTCUSDT
                    symbol_raw          = sym_raw,
                    last_price          = price,
                    funding_rate        = funding,
                    funding_rate_annual = self._calc_annual(funding),
                    open_interest_usd   = 0.0,              # sem batch disponível
                    volume_24h_usd      = vol_usd,
                    change_24h_pct      = chg,
                    bid_price           = None,
                    ask_price           = None,
                    spread_pct          = None,
                ))
            except Exception as e:
                logger.debug(f"[BINANCE] parse erro ticker {t.get('symbol','?')}: {e}")

        self._cache    = result
        self._cache_ts = now
        logger.info(f"[BINANCE] {len(result)} tickers carregados")
        return result

    def fetch_funding_rates(self) -> dict[str, float]:
        return self._fetch_funding_map()

    def fetch_orderbook(self, symbol_raw: str, depth: int = 20) -> dict:
        try:
            r = requests.get(
                f"{BASE_URL}/depth",
                params={"symbol": symbol_raw, "limit": depth},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            data = r.json()
            # Binance: {"bids": [["price", "qty"], ...], "asks": [...]}
            bids = [[self._safe_float(b[0]), self._safe_float(b[1])] for b in data.get("bids", [])]
            asks = [[self._safe_float(a[0]), self._safe_float(a[1])] for a in data.get("asks", [])]
            return {"bids": bids, "asks": asks}
        except Exception as e:
            logger.error(f"[BINANCE] fetch_orderbook {symbol_raw} erro: {e}")
            return {"bids": [], "asks": []}

    def fetch_recent_trades(self, symbol_raw: str, limit: int = 100) -> list[dict]:
        try:
            r = requests.get(
                f"{BASE_URL}/trades",
                params={"symbol": symbol_raw, "limit": min(limit, 1000)},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            trades = r.json()
            # Binance: [{"price": str, "qty": str, "isBuyerMaker": bool, "time": int}, ...]
            return [
                {
                    "price":     self._safe_float(tr.get("price")),
                    "qty":       self._safe_float(tr.get("qty")),
                    "is_buy":    not tr.get("isBuyerMaker", False),
                    "timestamp": int(tr.get("time", 0)),
                }
                for tr in trades
            ]
        except Exception as e:
            logger.error(f"[BINANCE] fetch_recent_trades {symbol_raw} erro: {e}")
            return []

    def fetch_klines(self, symbol_raw: str, interval: str = "15m", limit: int = 50) -> list[dict]:
        bn_interval = KLINE_INTERVALS.get(interval, "15m")
        try:
            r = requests.get(
                f"{BASE_URL}/klines",
                params={"symbol": symbol_raw, "interval": bn_interval, "limit": limit},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            # Binance: [[openTime, open, high, low, close, volume, closeTime, ...], ...]
            return [
                {
                    "timestamp": int(k[0]),
                    "open":      self._safe_float(k[1]),
                    "high":      self._safe_float(k[2]),
                    "low":       self._safe_float(k[3]),
                    "close":     self._safe_float(k[4]),
                    "volume":    self._safe_float(k[5]),
                }
                for k in r.json()
            ]
        except Exception as e:
            logger.error(f"[BINANCE] fetch_klines {symbol_raw} erro: {e}")
            return []
