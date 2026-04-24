"""
FuturesGuard — valida se um símbolo pode ser operado em futuros com liquidez
adequada antes de qualquer alerta ser enviado ao Telegram.

Design:
- Cache TTL 1h de fetch_markets() por exchange
- Fail-closed: se a consulta falhar, BLOQUEIA o alerta (evita spam em falhas de rede)
- Threshold de volume 24h em USD configurável via MIN_FUTURES_VOLUME_24H_USD
"""

import logging
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Configuração (ajustável)
MIN_FUTURES_VOLUME_24H_USD = 10_000_000  # $10M em volume 24h mínimo
CACHE_TTL_SECONDS = 3600  # 1h

# Prioridade de exchanges a consultar (MEXC primeiro porque é a principal do bot)
EXCHANGE_PRIORITY = ["mexc", "binance", "bybit", "okx", "gateio"]

# Cache: {exchange_name: (timestamp, {normalized_symbol: market_dict})}
_cache: Dict[str, Tuple[float, Dict[str, dict]]] = {}


def _normalize_symbol(sym: str) -> str:
    """BTC/USDT:USDT → BTCUSDT ; BTC_USDT → BTCUSDT ; BTCUSDT → BTCUSDT."""
    if not sym:
        return ""
    s = str(sym).upper()
    # Remove sufixo CCXT de swap (':USDT')
    if ":" in s:
        s = s.split(":", 1)[0]
    return s.replace("/", "").replace("_", "").replace("-", "")


def _fetch_markets_cached(exchange_name: str, fetch_fn) -> Optional[Dict[str, dict]]:
    """
    Retorna dict normalizado {BTCUSDT: market_dict}. Cache por 1h.
    fetch_fn é callable zero-arg que retorna lista de markets CCXT.
    """
    now = time.time()
    entry = _cache.get(exchange_name)
    if entry and (now - entry[0]) < CACHE_TTL_SECONDS:
        return entry[1]

    try:
        markets = fetch_fn()  # pode levantar exception de rede
    except Exception as e:
        logger.warning(f"[FuturesGuard] fetch_markets({exchange_name}) falhou: {e}")
        if entry:
            return entry[1]  # cache velho é melhor que nada
        return None  # sinal pra fail-closed

    # Aceita lista (fetch_markets retorna list) ou dict (exchange.markets)
    if isinstance(markets, dict):
        markets_iter = markets.values()
    else:
        markets_iter = markets

    by_sym = {}
    for m in markets_iter:
        if not isinstance(m, dict):
            continue
        if not m.get("active", True):  # se campo ausente, assume ativo
            continue
        # CCXT: swap = perpetual; future = dated. Incluir ambos.
        mtype = m.get("type")
        if mtype not in ("swap", "future"):
            # MEXC CCXT pode não expor 'type'; aceitar se 'swap' in id/symbol
            if not (m.get("swap") or m.get("future")):
                continue
        sym = _normalize_symbol(m.get("symbol", ""))
        if not sym:
            continue
        by_sym[sym] = m

    _cache[exchange_name] = (now, by_sym)
    return by_sym


def _extract_volume_usd(market: dict) -> float:
    """Extrai volume 24h em USD de um market CCXT. Tolerante a formatos."""
    info = market.get("info", {}) or {}
    for key in ("quoteVolume", "volume24h", "volumeQuote24h", "vol24h",
                "turnover24h", "volCcy24h", "volValue"):
        v = info.get(key) or market.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return 0.0


def check(symbol: str, exchange_fetchers: Dict[str, callable],
          direction: str = "SHORT") -> Tuple[bool, str]:
    """
    Valida se `symbol` pode ser operado em futuros.

    Args:
        symbol: ticker em qualquer formato (BTCUSDT, BTC/USDT:USDT, etc).
        exchange_fetchers: {exchange_name: callable_que_retorna_markets}.
            Ex: {"mexc": lambda: mexc.fetch_markets()}.
        direction: 'SHORT' ou 'LONG'. Só afeta log.

    Returns:
        (ok, reason):
            ok=True → pode enviar alerta
            ok=False → bloquear, `reason` é string curta pra log
    """
    norm = _normalize_symbol(symbol)
    if not norm:
        return False, "empty_symbol"

    best_volume = 0.0
    found_any = False
    for ex_name in EXCHANGE_PRIORITY:
        fetch_fn = exchange_fetchers.get(ex_name)
        if not fetch_fn:
            continue
        markets_by_sym = _fetch_markets_cached(ex_name, fetch_fn)
        if markets_by_sym is None:
            continue
        m = markets_by_sym.get(norm)
        if not m:
            continue
        found_any = True
        vol = _extract_volume_usd(m)
        if vol > best_volume:
            best_volume = vol
        if vol >= MIN_FUTURES_VOLUME_24H_USD:
            return True, f"ok_{ex_name}_vol=${vol:,.0f}"

    if not found_any:
        return False, f"no_futures_pair_{norm}"
    return False, (
        f"volume_too_low_{norm}_best=${best_volume:,.0f}"
        f"_min=${MIN_FUTURES_VOLUME_24H_USD:,.0f}"
    )


def stats() -> dict:
    """Debug: estado atual do cache."""
    return {
        ex: {
            "last_fetch_age_s": int(time.time() - ts),
            "markets_count": len(markets),
        }
        for ex, (ts, markets) in _cache.items()
    }
