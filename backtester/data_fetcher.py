"""
backtester/data_fetcher.py — Busca histórico OHLCV paginado para backtesting.

Retorna DataFrames para 15m, 1h, 4h e 1d com dados suficientes para o período solicitado.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)

# Número máximo de candles por request CCXT (MEXC limita em 1000)
_MAX_PER_REQUEST = 1000

# Candles por TF por dia
_CANDLES_PER_DAY = {
    "15m": 96,
    "1h":  24,
    "4h":   6,
    "1d":   1,
}

# Label CCXT → label interno
_TF_MAP = {
    "15m": "15m",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1d",
}


def _fetch_paginated(exchange, symbol: str, timeframe: str, total_candles: int) -> pd.DataFrame:
    """
    Busca `total_candles` candles históricos do par `symbol` no TF `timeframe`,
    paginando com o parâmetro `since` do CCXT.
    """
    all_rows = []
    # timestamp de início = agora − total_candles × duração_do_tf
    tf_duration_ms = {
        "15m":  15 * 60 * 1000,
        "1h":   60 * 60 * 1000,
        "4h":  240 * 60 * 1000,
        "1d": 1440 * 60 * 1000,
    }
    duration_ms = tf_duration_ms[timeframe]
    since_ms    = int(datetime.now(timezone.utc).timestamp() * 1000) - total_candles * duration_ms

    while len(all_rows) < total_candles:
        remaining = total_candles - len(all_rows)
        limit     = min(remaining, _MAX_PER_REQUEST)
        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
        except Exception as e:
            log.warning(f"[data_fetcher] fetch_ohlcv {symbol} {timeframe} erro: {e}")
            break

        if not raw:
            break

        all_rows.extend(raw)
        last_ts = raw[-1][0]

        # Avança since para o próximo bloco
        since_ms = last_ts + duration_ms

        # Se retornou menos que o limit, não há mais dados
        if len(raw) < limit:
            break

        # Respeita rate limits
        time.sleep(0.35)

    if not all_rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def fetch_all_timeframes(symbol: str = "BTC/USDT:USDT", days: int = 180) -> dict:
    """
    Busca dados históricos para todos os timeframes necessários para o backtest.

    Retorna dict com chaves '15m', '1h', '4h', '1d'.
    Adiciona margem de 20% para garantir candles suficientes para o lookback.
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from mexc_client import get_exchange

    exchange = get_exchange()
    dfs      = {}

    for tf, cpd in _CANDLES_PER_DAY.items():
        # Margem extra para garantir o lookback de 300 candles
        total = int(days * cpd * 1.15) + 300
        log.info(f"[data_fetcher] Buscando {total} candles de {symbol} {tf} ...")
        df = _fetch_paginated(exchange, symbol, tf, total)
        log.info(f"[data_fetcher]   → {len(df)} candles obtidos ({df.index[0] if len(df) else 'N/A'} a {df.index[-1] if len(df) else 'N/A'})")
        dfs[tf] = df
        time.sleep(0.35)

    return dfs
