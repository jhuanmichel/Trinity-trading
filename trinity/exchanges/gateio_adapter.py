"""
gateio_adapter.py — Adapter Gate.io Futures (USDT-M)

Campos confirmados via probe (2026-04-16), 665 contratos:
  contract             : "ETH_USDT"    (underscore, USDT-settled)
  last                 : str           preço atual
  funding_rate         : str           funding rate decimal
  total_size           : str           OI em contratos
  volume_24h_quote     : str           volume 24h em USDT
  change_percentage    : str           variação 24h já em % (ex: "1.46")
  highest_bid          : str           melhor bid
  lowest_ask           : str           melhor ask
  volume_24h           : str           volume 24h em contratos (usado para calcular OI USD)

OI USD = total_size * (volume_24h_quote / volume_24h) — preço médio por contrato
Endpoint público — sem API key.
"""

import logging
import time
import requests

from .base_adapter import ExchangeAdapter, NormalizedTicker

logger = logging.getLogger(__name__)

BASE_URL = "https://api.gateio.ws/api/v4/futures/usdt"
TIMEOUT  = 15
UA       = "Trinity/5.0"

KLINE_INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "1h", "4h": "4h", "1d": "1d",
}


class GateioAdapter(ExchangeAdapter):

    def __init__(self):
        self._cache: list[NormalizedTicker] = []
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 5.0

    @property
    def name(self) -> str:
        return "gateio"

    def fetch_all_tickers(self) -> list[NormalizedTicker]:
        now = time.time()
        if now - self._cache_ts < self._cache_ttl and self._cache:
            return self._cache

        try:
            r = requests.get(
                f"{BASE_URL}/tickers",
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            raw_list = r.json()
        except Exception as e:
            logger.error(f"[GATEIO] fetch_all_tickers erro: {e}")
            return self._cache

        result = []
        for t in raw_list:
            try:
                contract = t.get("contract", "")
                # Filtrar: só perpétuos USDT
                if not contract.endswith("_USDT"):
                    continue

                price = self._safe_float(t.get("last"))
                if price <= 0:
                    continue

                funding = self._safe_float(t.get("funding_rate"))
                vol_usd = self._safe_float(t.get("volume_24h_quote"))
                chg     = self._safe_float(t.get("change_percentage"))  # já em %
                bid     = self._safe_float(t.get("highest_bid"))
                ask     = self._safe_float(t.get("lowest_ask"))

                # OI USD: total_size (contratos) * (volume_24h_quote / volume_24h) = preço médio/contrato
                total_size = self._safe_float(t.get("total_size"))
                vol_24h    = self._safe_float(t.get("volume_24h"))
                if vol_24h > 0 and total_size > 0:
                    avg_contract_price = vol_usd / vol_24h
                    oi_usd = total_size * avg_contract_price
                else:
                    oi_usd = 0.0

                sym = self.normalize_symbol(contract)  # "ETH_USDT" → "ETHUSDT"

                result.append(NormalizedTicker(
                    exchange            = "gateio",
                    symbol              = sym,
                    symbol_raw          = contract,
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
                logger.debug(f"[GATEIO] parse erro ticker {t.get('contract','?')}: {e}")

        self._cache    = result
        self._cache_ts = now
        logger.info(f"[GATEIO] {len(result)} tickers carregados")
        return result

    def fetch_funding_rates(self) -> dict[str, float]:
        return {t.symbol: t.funding_rate for t in self.fetch_all_tickers()}

    def fetch_orderbook(self, symbol_raw: str, depth: int = 20) -> dict:
        try:
            r = requests.get(
                f"{BASE_URL}/order_book",
                params={"contract": symbol_raw, "limit": depth},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            data = r.json()
            # Gate.io: {"bids": [{"p": "price", "s": size}, ...], "asks": [...]}
            bids = [[self._safe_float(b.get("p")), self._safe_float(b.get("s"))]
                    for b in data.get("bids", [])]
            asks = [[self._safe_float(a.get("p")), self._safe_float(a.get("s"))]
                    for a in data.get("asks", [])]
            return {"bids": bids, "asks": asks}
        except Exception as e:
            logger.error(f"[GATEIO] fetch_orderbook {symbol_raw} erro: {e}")
            return {"bids": [], "asks": []}

    def fetch_recent_trades(self, symbol_raw: str, limit: int = 100) -> list[dict]:
        try:
            r = requests.get(
                f"{BASE_URL}/trades",
                params={"contract": symbol_raw, "limit": min(limit, 1000)},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            trades = r.json()
            # Gate.io: [{"price": str, "size": int (signed), "id": int, "create_time": float}, ...]
            return [
                {
                    "price":     self._safe_float(tr.get("price")),
                    "qty":       abs(self._safe_float(tr.get("size", 0))),
                    "is_buy":    self._safe_float(tr.get("size", 0)) > 0,
                    "timestamp": int(self._safe_float(tr.get("create_time", 0)) * 1000),
                }
                for tr in trades
            ]
        except Exception as e:
            logger.error(f"[GATEIO] fetch_recent_trades {symbol_raw} erro: {e}")
            return []

    def fetch_klines(self, symbol_raw: str, interval: str = "15m", limit: int = 50) -> list[dict]:
        gt_interval = KLINE_INTERVALS.get(interval, "15m")
        try:
            r = requests.get(
                f"{BASE_URL}/candlesticks",
                params={"contract": symbol_raw, "interval": gt_interval, "limit": limit},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            raw = r.json()
            # Gate.io: [{"t": ts, "o": open, "h": high, "l": low, "c": close, "v": vol}, ...]
            return [
                {
                    "timestamp": int(self._safe_float(k.get("t", 0))),
                    "open":      self._safe_float(k.get("o")),
                    "high":      self._safe_float(k.get("h")),
                    "low":       self._safe_float(k.get("l")),
                    "close":     self._safe_float(k.get("c")),
                    "volume":    self._safe_float(k.get("v")),
                }
                for k in raw
            ]
        except Exception as e:
            logger.error(f"[GATEIO] fetch_klines {symbol_raw} erro: {e}")
            return []
