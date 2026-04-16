"""
mexc_adapter.py — Adapter MEXC Futures

Campos confirmados via probe (2026-04-16), 867 contratos:
  symbol       : "BTC_USDT"  (underscore)
  lastPrice    : int/float   preço atual
  fundingRate  : float       já em decimal (ex: -2.5e-05)
  holdVol      : int         OI em contratos (aproximado para USD)
  amount24     : float       volume 24h em USDT
  riseFallRate : float       variação 24h decimal (0.0121 = +1.21%)
  bid1         : float       melhor bid
  ask1         : int/float   melhor ask

Endpoints públicos — sem API key.
"""

import logging
import time
import requests

from .base_adapter import ExchangeAdapter, NormalizedTicker

logger = logging.getLogger(__name__)

BASE_URL = "https://contract.mexc.com/api/v1/contract"
TIMEOUT  = 15
UA       = "Trinity/5.0"

# Mapeamento de intervalos Trinity → MEXC
KLINE_INTERVALS = {
    "1m": "Min1", "5m": "Min5", "15m": "Min15",
    "1h": "Min60", "4h": "Hour4", "1d": "Day1",
}


class MexcAdapter(ExchangeAdapter):

    def __init__(self):
        self._cache: list[NormalizedTicker] = []
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 5.0

    @property
    def name(self) -> str:
        return "mexc"

    def fetch_all_tickers(self) -> list[NormalizedTicker]:
        now = time.time()
        if now - self._cache_ts < self._cache_ttl and self._cache:
            return self._cache

        try:
            r = requests.get(
                f"{BASE_URL}/ticker",
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            data = r.json().get("data", [])
        except Exception as e:
            logger.error(f"[MEXC] fetch_all_tickers erro: {e}")
            return self._cache  # retorna cache antigo em falha

        result = []
        for t in data:
            try:
                sym_raw = t.get("symbol", "")
                # Filtrar: só contratos USDT perpétuos
                if not sym_raw.endswith("_USDT"):
                    continue

                sym = self.normalize_symbol(sym_raw)
                price   = self._safe_float(t.get("lastPrice"))
                funding = self._safe_float(t.get("fundingRate"))
                hold    = self._safe_float(t.get("holdVol"))
                vol_usd = self._safe_float(t.get("amount24"))
                rfr     = self._safe_float(t.get("riseFallRate"))
                bid     = self._safe_float(t.get("bid1"))
                ask     = self._safe_float(t.get("ask1"))

                if price <= 0:
                    continue

                # OI em USD: holdVol * price (aproximado — desconsidera tamanho do contrato)
                oi_usd = hold * price

                result.append(NormalizedTicker(
                    exchange            = "mexc",
                    symbol              = sym,
                    symbol_raw          = sym_raw,
                    last_price          = price,
                    funding_rate        = funding,
                    funding_rate_annual = self._calc_annual(funding),
                    open_interest_usd   = oi_usd,
                    volume_24h_usd      = vol_usd,
                    change_24h_pct      = rfr * 100,
                    bid_price           = bid if bid > 0 else None,
                    ask_price           = ask if ask > 0 else None,
                    spread_pct          = self._calc_spread(bid, ask),
                ))
            except Exception as e:
                logger.debug(f"[MEXC] parse erro ticker {t.get('symbol','?')}: {e}")

        self._cache    = result
        self._cache_ts = now
        logger.info(f"[MEXC] {len(result)} tickers carregados")
        return result

    def fetch_funding_rates(self) -> dict[str, float]:
        return {t.symbol: t.funding_rate for t in self.fetch_all_tickers()}

    def fetch_orderbook(self, symbol_raw: str, depth: int = 20) -> dict:
        try:
            r = requests.get(
                f"{BASE_URL}/depth/{symbol_raw}",
                params={"limit": depth},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            # MEXC depth: {"asks": [{"price": x, "vol": y}, ...], "bids": [...]}
            bids = [[self._safe_float(b.get("price")), self._safe_float(b.get("vol"))]
                    for b in data.get("bids", [])]
            asks = [[self._safe_float(a.get("price")), self._safe_float(a.get("vol"))]
                    for a in data.get("asks", [])]
            return {"bids": bids, "asks": asks}
        except Exception as e:
            logger.error(f"[MEXC] fetch_orderbook {symbol_raw} erro: {e}")
            return {"bids": [], "asks": []}

    def fetch_recent_trades(self, symbol_raw: str, limit: int = 100) -> list[dict]:
        try:
            r = requests.get(
                f"{BASE_URL}/deals/{symbol_raw}",
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            deals = r.json().get("data", {}).get("deals", [])[:limit]
            # MEXC deals: {"p": price, "v": qty (signed), "T": timestamp, "M": 1=buy,2=sell}
            return [
                {
                    "price":     self._safe_float(d.get("p")),
                    "qty":       abs(self._safe_float(d.get("v"))),
                    "is_buy":    d.get("M") == 1,
                    "timestamp": int(d.get("T", 0)),
                }
                for d in deals
            ]
        except Exception as e:
            logger.error(f"[MEXC] fetch_recent_trades {symbol_raw} erro: {e}")
            return []

    def fetch_klines(self, symbol_raw: str, interval: str = "15m", limit: int = 50) -> list[dict]:
        mx_interval = KLINE_INTERVALS.get(interval, "Min15")
        try:
            r = requests.get(
                f"{BASE_URL}/kline/{symbol_raw}",
                params={"interval": mx_interval},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            # MEXC kline: dict com arrays paralelos {time:[...], open:[...], ...}
            times  = data.get("time",  [])
            opens  = data.get("open",  [])
            highs  = data.get("high",  [])
            lows   = data.get("low",   [])
            closes = data.get("close", [])
            vols   = data.get("vol",   [])
            candles = []
            for i in range(min(limit, len(times))):
                candles.append({
                    "timestamp": int(times[i]),
                    "open":      self._safe_float(opens[i] if i < len(opens) else 0),
                    "high":      self._safe_float(highs[i] if i < len(highs) else 0),
                    "low":       self._safe_float(lows[i]  if i < len(lows)  else 0),
                    "close":     self._safe_float(closes[i] if i < len(closes) else 0),
                    "volume":    self._safe_float(vols[i]  if i < len(vols)  else 0),
                })
            return candles
        except Exception as e:
            logger.error(f"[MEXC] fetch_klines {symbol_raw} erro: {e}")
            return []
