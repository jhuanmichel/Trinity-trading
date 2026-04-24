"""
exchange_registry — provedor lazy de clients CCXT para o FuturesGuard.

Centralizado aqui para evitar que cada caller instancie seu proprio client
ccxt. Mantem singleton por exchange e expoe fetch_markets() zero-arg.

Comeca com MEXC (principal exchange do bot). Adicionar mais exchanges aqui
quando necessario (binance, bybit, okx, gateio).
"""
from __future__ import annotations

import logging
from typing import Callable, Dict

logger = logging.getLogger(__name__)

_clients: dict = {}


def _get_mexc_client():
    if "mexc" in _clients:
        return _clients["mexc"]
    try:
        import ccxt
        ex = ccxt.mexc({"options": {"defaultType": "swap"}})
        _clients["mexc"] = ex
        return ex
    except Exception as e:
        logger.warning(f"[ExchangeRegistry] MEXC init falhou: {e}")
        return None


def _fetch_mexc_markets():
    ex = _get_mexc_client()
    if ex is None:
        return []
    # ccxt.fetch_markets retorna lista. fetch_tickers popula volume24h.
    markets = ex.fetch_markets()
    # Enriquecer com tickers para obter quoteVolume (fetch_markets nao traz)
    try:
        tickers = ex.fetch_tickers()
    except Exception as e:
        logger.info(f"[ExchangeRegistry] MEXC fetch_tickers skip: {e}")
        return markets
    # Merge volume do ticker no market dict
    by_sym = {m.get("symbol"): m for m in markets if m.get("symbol")}
    for sym, tk in tickers.items():
        m = by_sym.get(sym)
        if not m:
            continue
        qv = tk.get("quoteVolume") or tk.get("info", {}).get("volCcy24h") or 0
        if qv:
            m.setdefault("info", {})
            m["info"]["quoteVolume"] = qv
            m["quoteVolume"] = qv
    return list(by_sym.values())


def get_exchange_fetchers() -> Dict[str, Callable]:
    """
    Retorna dict {exchange_name: fetch_markets_fn} para FuturesGuard.

    Hoje so MEXC esta wireado. Adicionar binance/bybit/okx/gateio
    quando tivermos uso para alem do MEXC.
    """
    return {
        "mexc": _fetch_mexc_markets,
    }
