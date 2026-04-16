"""
bybit_adapter.py — Adapter Bybit Futures (Linear / USDT-M)

Campos confirmados via probe (2026-04-16), 645 contratos:
  symbol           : "BTCUSDT"   (perpétuos USDT — sem data de vencimento)
  lastPrice        : str         preço atual
  fundingRate      : str         funding rate decimal
  openInterestValue: str         OI em USD
  turnover24h      : str         volume 24h em USDT
  price24hPcnt     : str         variação 24h decimal (0.002009 = +0.2009%)
  bid1Price        : str         melhor bid
  ask1Price        : str         melhor ask

Endpoint público — sem API key.
"""

import logging
import time
import requests

from .base_adapter import ExchangeAdapter, NormalizedTicker

logger = logging.getLogger(__name__)

BASE_URL = "https://api.bybit.com/v5"
TIMEOUT  = 15
UA       = "Trinity/5.0"

KLINE_INTERVALS = {
    "1m": "1", "5m": "5", "15m": "15",
    "1h": "60", "4h": "240", "1d": "D",
}


class BybitAdapter(ExchangeAdapter):

    def __init__(self):
        self._cache: list[NormalizedTicker] = []
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 5.0

    @property
    def name(self) -> str:
        return "bybit"

    def fetch_all_tickers(self) -> list[NormalizedTicker]:
        now = time.time()
        if now - self._cache_ts < self._cache_ttl and self._cache:
            return self._cache

        try:
            r = requests.get(
                f"{BASE_URL}/market/tickers",
                params={"category": "linear"},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            raw_list = r.json().get("result", {}).get("list", [])
        except Exception as e:
            logger.error(f"[BYBIT] fetch_all_tickers erro: {e}")
            return self._cache

        result = []
        for t in raw_list:
            try:
                sym_raw = t.get("symbol", "")
                # Filtrar: só perpétuos USDT (perpétuos não têm dígitos no sufixo)
                if not sym_raw.endswith("USDT"):
                    continue
                # Excluir deliveries que têm dígitos (ex: "BTC-28MAR25")
                if any(c.isdigit() for c in sym_raw.replace("USDT", "")):
                    continue

                price   = self._safe_float(t.get("lastPrice"))
                if price <= 0:
                    continue

                funding = self._safe_float(t.get("fundingRate"))
                oi_usd  = self._safe_float(t.get("openInterestValue"))
                vol_usd = self._safe_float(t.get("turnover24h"))
                chg     = self._safe_float(t.get("price24hPcnt")) * 100  # decimal → %
                bid     = self._safe_float(t.get("bid1Price"))
                ask     = self._safe_float(t.get("ask1Price"))

                result.append(NormalizedTicker(
                    exchange            = "bybit",
                    symbol              = sym_raw,          # já normalizado: BTCUSDT
                    symbol_raw          = sym_raw,
                    last_price          = price,
                    funding_rate        = funding,
                    funding_rate_annual = self._calc_annual(funding),
                    open_interest_usd   = oi_usd,
                    volume_24h_usd      = vol_usd,
                    change_24h_pct      = chg,
                    bid_price           = bid if bid > 0 else None,
                    ask_price           = ask if ask > 0 else None,
                    spread_pct          = self._calc_spread(bid, ask),
                ))
            except Exception as e:
                logger.debug(f"[BYBIT] parse erro ticker {t.get('symbol','?')}: {e}")

        self._cache    = result
        self._cache_ts = now
        logger.info(f"[BYBIT] {len(result)} tickers carregados")
        return result

    def fetch_funding_rates(self) -> dict[str, float]:
        return {t.symbol: t.funding_rate for t in self.fetch_all_tickers()}

    def fetch_orderbook(self, symbol_raw: str, depth: int = 20) -> dict:
        try:
            r = requests.get(
                f"{BASE_URL}/market/orderbook",
                params={"category": "linear", "symbol": symbol_raw, "limit": depth},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            data = r.json().get("result", {})
            # Bybit: {"b": [["price", "qty"], ...], "a": [...]}
            bids = [[self._safe_float(b[0]), self._safe_float(b[1])] for b in data.get("b", [])]
            asks = [[self._safe_float(a[0]), self._safe_float(a[1])] for a in data.get("a", [])]
            return {"bids": bids, "asks": asks}
        except Exception as e:
            logger.error(f"[BYBIT] fetch_orderbook {symbol_raw} erro: {e}")
            return {"bids": [], "asks": []}

    def fetch_recent_trades(self, symbol_raw: str, limit: int = 100) -> list[dict]:
        try:
            r = requests.get(
                f"{BASE_URL}/market/recent-trade",
                params={"category": "linear", "symbol": symbol_raw, "limit": min(limit, 500)},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            trades = r.json().get("result", {}).get("list", [])
            # Bybit: [{"price": str, "size": str, "side": "Buy"|"Sell", "time": str}, ...]
            return [
                {
                    "price":     self._safe_float(tr.get("price")),
                    "qty":       self._safe_float(tr.get("size")),
                    "is_buy":    tr.get("side") == "Buy",
                    "timestamp": int(self._safe_float(tr.get("time", 0))),
                }
                for tr in trades
            ]
        except Exception as e:
            logger.error(f"[BYBIT] fetch_recent_trades {symbol_raw} erro: {e}")
            return []

    def fetch_klines(self, symbol_raw: str, interval: str = "15m", limit: int = 50) -> list[dict]:
        bb_interval = KLINE_INTERVALS.get(interval, "15")
        try:
            r = requests.get(
                f"{BASE_URL}/market/kline",
                params={
                    "category": "linear",
                    "symbol":   symbol_raw,
                    "interval": bb_interval,
                    "limit":    limit,
                },
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            raw = r.json().get("result", {}).get("list", [])
            # Bybit: [[startTime, open, high, low, close, volume, turnover], ...]
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
            logger.error(f"[BYBIT] fetch_klines {symbol_raw} erro: {e}")
            return []
