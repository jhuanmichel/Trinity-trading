"""
base_adapter.py — Interface abstrata para exchanges.

Todas as exchanges retornam dados no MESMO formato normalizado.
O resto do Trinity (scanners, detectores, scoring) consome este formato
sem saber qual exchange gerou os dados.

Campos confirmados via probe real das APIs (2026-04-16):
  MEXC   : symbol, lastPrice, fundingRate, holdVol, amount24, riseFallRate, bid1, ask1
  Binance: symbol, lastPrice, quoteVolume, priceChangePercent — funding via /premiumIndex
  Bybit  : symbol, lastPrice, fundingRate, openInterestValue, turnover24h, price24hPcnt
  OKX    : instId, last, volCcy24h, open24h, bidPx, askPx — sem funding em batch
  Gate.io: contract, last, funding_rate, total_size, volume_24h_quote, change_percentage
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NormalizedTicker:
    """Formato unificado de ticker. Todos os campos obrigatórios exceto os Optional."""
    exchange:             str    # "mexc" | "binance" | "bybit" | "okx" | "gateio"
    symbol:               str    # formato unificado: "BTCUSDT" (sem underscore, sem SWAP)
    symbol_raw:           str    # formato original da exchange: "BTC_USDT", "BTC-USDT-SWAP"
    last_price:           float  # preço atual em USD
    funding_rate:         float  # funding rate atual (decimal, ex: 0.0001 = 0.01%)
    funding_rate_annual:  float  # funding rate anualizado (rate * 3 * 365 * 100)
    open_interest_usd:    float  # OI em USD (aproximado quando necessário)
    volume_24h_usd:       float  # volume 24h em USD
    change_24h_pct:       float  # variação 24h em % (ex: 5.3 = +5.3%)
    change_7d_pct:        Optional[float] = None
    bid_price:            Optional[float] = None
    ask_price:            Optional[float] = None
    spread_pct:           Optional[float] = None  # (ask-bid)/mid * 100


class ExchangeAdapter(ABC):
    """Interface que cada exchange deve implementar."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Nome da exchange em lowercase: 'mexc', 'binance', etc."""

    @abstractmethod
    def fetch_all_tickers(self) -> list[NormalizedTicker]:
        """
        Buscar TODOS os tickers de futuros perpétuos em 1–2 requests.
        Retornar lista de NormalizedTicker. Filtrar contratos inativos/expirados.
        NÃO filtrar por volume — o scanner decide.
        Cache de 5s internamente.
        """

    @abstractmethod
    def fetch_funding_rates(self) -> dict[str, float]:
        """
        Retornar dict {symbol_normalizado: funding_rate} para todos os contratos.
        Se funding já vem no ticker, extrai de fetch_all_tickers().
        """

    @abstractmethod
    def fetch_orderbook(self, symbol_raw: str, depth: int = 20) -> dict:
        """
        Retornar orderbook normalizado:
        {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
        """

    @abstractmethod
    def fetch_recent_trades(self, symbol_raw: str, limit: int = 100) -> list[dict]:
        """
        Retornar trades normalizados:
        [{"price": float, "qty": float, "is_buy": bool, "timestamp": int}, ...]
        """

    @abstractmethod
    def fetch_klines(self, symbol_raw: str, interval: str = "15m", limit: int = 50) -> list[dict]:
        """
        Retornar candles normalizados:
        [{"open": float, "high": float, "low": float, "close": float,
          "volume": float, "timestamp": int}, ...]
        Intervalo: "1m", "5m", "15m", "1h", "4h", "1d"
        """

    def normalize_symbol(self, raw: str) -> str:
        """Converter símbolo raw da exchange para formato unificado BTCUSDT."""
        s = raw.upper()
        s = s.replace("_", "").replace("-", "")
        # Remover sufixos de contratos
        for suffix in ("SWAP", "PERP", "PERPETUAL"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
        return s

    def _safe_float(self, val, default: float = 0.0) -> float:
        """Conversão segura para float."""
        try:
            return float(val) if val is not None else default
        except (ValueError, TypeError):
            return default

    def _calc_annual(self, funding_rate: float) -> float:
        """Calcula funding rate anualizado (3 pagamentos/dia * 365 dias * 100%)."""
        return funding_rate * 3 * 365 * 100

    def _calc_spread(self, bid: float, ask: float) -> Optional[float]:
        """Calcula spread em % sobre o mid price."""
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            return (ask - bid) / mid * 100 if mid > 0 else None
        return None
