"""
exchange_manager.py — Gerenciador Multi-Exchange Trinity v5

Responsabilidades:
1. Instanciar e manter referência a todos os adapters ativos
2. Buscar tickers de TODAS as exchanges em paralelo (ThreadPoolExecutor)
3. Agregar e deduplicar por símbolo normalizado
4. Comparar funding rates cross-exchange para arbitragem
5. Servir dados unificados para o resto do Trinity

Fail-open: falha de uma exchange NÃO afeta as outras.
Cache de 5s no scan unificado + nos adapters individuais.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .base_adapter import ExchangeAdapter, NormalizedTicker

logger = logging.getLogger(__name__)


class ExchangeManager:
    """Gerencia múltiplas exchanges com interface unificada."""

    def __init__(self, adapters: list[ExchangeAdapter] = None):
        """
        Se adapters=None, instancia TODAS as exchanges disponíveis.
        Cada adapter é independente — falha de um não afeta os outros.
        """
        if adapters is None:
            self._adapters = self._init_all_adapters()
        else:
            self._adapters = {a.name: a for a in adapters}

        self._last_unified: dict[str, list[NormalizedTicker]] = {}
        self._last_scan_ts: float = 0.0
        self._scan_ttl:     float = 5.0

        logger.info(
            f"[EXCHANGE_MGR] Inicializado com {len(self._adapters)} exchanges: "
            f"{', '.join(self._adapters.keys())}"
        )

    # ── Inicialização ─────────────────────────────────────────────────────────

    def _init_all_adapters(self) -> dict[str, ExchangeAdapter]:
        """Instancia todos os adapters. Fail-open por exchange."""
        adapters: dict[str, ExchangeAdapter] = {}
        adapter_classes = [
            ("mexc",    "trinity.exchanges.mexc_adapter",    "MexcAdapter"),
            ("binance", "trinity.exchanges.binance_adapter", "BinanceAdapter"),
            ("bybit",   "trinity.exchanges.bybit_adapter",   "BybitAdapter"),
            ("okx",     "trinity.exchanges.okx_adapter",     "OkxAdapter"),
            ("gateio",  "trinity.exchanges.gateio_adapter",  "GateioAdapter"),
        ]
        for name, module_path, class_name in adapter_classes:
            try:
                import importlib
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name)
                adapters[name] = cls()
                logger.info(f"[EXCHANGE_MGR] {name} adapter carregado")
            except Exception as e:
                logger.warning(f"[EXCHANGE_MGR] {name} adapter FALHOU ao carregar: {e}")
        return adapters

    # ── Scan Unificado ────────────────────────────────────────────────────────

    def fetch_all_tickers_unified(self) -> dict[str, list[NormalizedTicker]]:
        """
        Busca tickers de TODAS as exchanges em paralelo.

        Retorna dict:
        {
            "BTCUSDT": [NormalizedTicker(exchange="mexc",...), NormalizedTicker(exchange="binance",...)],
            "ETHUSDT": [...],
        }

        Cada símbolo pode ter múltiplos tickers (um por exchange que lista o ativo).
        Resultado é cacheado por 5s.
        """
        now = time.time()
        if now - self._last_scan_ts < self._scan_ttl and self._last_unified:
            return self._last_unified

        per_exchange: dict[str, list[NormalizedTicker]] = {}

        with ThreadPoolExecutor(max_workers=len(self._adapters)) as executor:
            futures = {
                executor.submit(adapter.fetch_all_tickers): name
                for name, adapter in self._adapters.items()
            }
            for future in as_completed(futures, timeout=30):
                name = futures[future]
                try:
                    tickers = future.result()
                    per_exchange[name] = tickers
                    logger.info(f"[EXCHANGE_MGR] {name}: {len(tickers)} tickers")
                except Exception as e:
                    logger.error(f"[EXCHANGE_MGR] {name} falhou no scan: {e}")
                    per_exchange[name] = []

        # Agrupar por símbolo normalizado
        unified: dict[str, list[NormalizedTicker]] = {}
        for tickers in per_exchange.values():
            for t in tickers:
                unified.setdefault(t.symbol, []).append(t)

        self._last_unified = unified
        self._last_scan_ts = time.time()

        total_symbols = len(unified)
        total_tickers = sum(len(v) for v in unified.values())
        multi = sum(1 for v in unified.values() if len(v) >= 2)
        logger.info(
            f"[EXCHANGE_MGR] Scan unificado: {total_symbols} símbolos, "
            f"{total_tickers} tickers, {multi} em 2+ exchanges"
        )
        return unified

    # ── Funding Arbitrage ─────────────────────────────────────────────────────

    def get_funding_arbitrage_opportunities(
        self, min_spread_annual: float = 20.0
    ) -> list[dict]:
        """
        Encontra oportunidades de arbitragem de funding rate cross-exchange.

        Para cada símbolo em 2+ exchanges, calcula o spread de funding.
        Retorna lista ordenada por spread_annual_pct DESC.

        Retorna:
        [{
            "symbol":              "BTCUSDT",
            "long_exchange":       "mexc",        # funding menor/negativo — ir LONG aqui
            "short_exchange":      "binance",      # funding maior/positivo — ir SHORT aqui
            "long_funding_8h":     -0.0015,
            "short_funding_8h":    0.0008,
            "spread_8h":           0.0023,
            "spread_annual_pct":   31.4,
            "long_price":          83500.0,
            "short_price":         83520.0,
            "price_spread_pct":    0.024,
        }]
        """
        unified = self.fetch_all_tickers_unified()
        opps = []

        for symbol, tickers in unified.items():
            # Só símbolos com funding rate conhecida em 2+ exchanges
            with_funding = [t for t in tickers if t.funding_rate != 0.0 or len(tickers) >= 2]
            candidates   = [t for t in tickers if t.last_price > 0]
            if len(candidates) < 2:
                continue

            sorted_fr = sorted(candidates, key=lambda t: t.funding_rate)
            lowest  = sorted_fr[0]   # menor funding → ir LONG
            highest = sorted_fr[-1]  # maior funding → ir SHORT

            if lowest.exchange == highest.exchange:
                continue

            spread_8h    = highest.funding_rate - lowest.funding_rate
            spread_annual = spread_8h * 3 * 365 * 100

            if spread_annual < min_spread_annual:
                continue

            price_spread = abs(highest.last_price - lowest.last_price)
            mid_price    = (highest.last_price + lowest.last_price) / 2
            price_spread_pct = (price_spread / mid_price * 100) if mid_price > 0 else 0.0

            opps.append({
                "symbol":           symbol,
                "long_exchange":    lowest.exchange,
                "short_exchange":   highest.exchange,
                "long_funding_8h":  lowest.funding_rate,
                "short_funding_8h": highest.funding_rate,
                "spread_8h":        round(spread_8h, 8),
                "spread_annual_pct": round(spread_annual, 2),
                "long_price":       lowest.last_price,
                "short_price":      highest.last_price,
                "price_spread_pct": round(price_spread_pct, 4),
            })

        opps.sort(key=lambda x: x["spread_annual_pct"], reverse=True)
        return opps

    # ── Helpers ───────────────────────────────────────────────────────────────

    def get_best_ticker(self, symbol: str) -> Optional[NormalizedTicker]:
        """Retorna o ticker com maior volume para um símbolo (melhor liquidez)."""
        unified = self.fetch_all_tickers_unified()
        tickers = unified.get(symbol.upper(), [])
        if not tickers:
            return None
        return max(tickers, key=lambda t: t.volume_24h_usd)

    def get_adapter(self, exchange: str) -> Optional[ExchangeAdapter]:
        """Retorna adapter de uma exchange específica."""
        return self._adapters.get(exchange.lower())

    def get_ticker(self, symbol: str, exchange: str) -> Optional[NormalizedTicker]:
        """Retorna ticker de um símbolo em uma exchange específica."""
        unified = self.fetch_all_tickers_unified()
        for t in unified.get(symbol.upper(), []):
            if t.exchange == exchange.lower():
                return t
        return None

    def status_summary(self) -> dict:
        """Retorna resumo de status de todas as exchanges."""
        unified = self.fetch_all_tickers_unified()
        per_exchange: dict[str, int] = {}
        for tickers in unified.values():
            for t in tickers:
                per_exchange[t.exchange] = per_exchange.get(t.exchange, 0) + 1

        multi = sum(1 for v in unified.values() if len(v) >= 2)
        return {
            "exchanges":         self.active_exchanges,
            "tickers_per_exchange": per_exchange,
            "total_unique_symbols": len(unified),
            "symbols_multi_exchange": multi,
            "timestamp":         time.time(),
        }

    @property
    def active_exchanges(self) -> list[str]:
        return list(self._adapters.keys())


# ── Singleton global ──────────────────────────────────────────────────────────

_manager: Optional[ExchangeManager] = None


def get_exchange_manager() -> ExchangeManager:
    """Retorna singleton do ExchangeManager (lazy init)."""
    global _manager
    if _manager is None:
        _manager = ExchangeManager()
    return _manager
