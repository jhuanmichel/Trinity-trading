"""
_yf_safe.py — yfinance.download com timeout robusto.

Yahoo Finance throttle agressivo em IPs cloud (Render) faz `yf.download`
travar indefinidamente sem timeout nativo. Esse helper roda a chamada em
um ThreadPoolExecutor compartilhado e aborta após `timeout` segundos.

A thread subjacente fica orfã (não dá pra matar request bloqueado), mas o
caller libera. Em produção isso é aceitável: os outros yf.download usam o
mesmo pool, então no pior caso N chamadas paralelas hanging consomem N
slots — com max_workers=8 e ciclo de 60min, tem folga.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False
    log.warning("[yf-safe] yfinance não instalado — todas as chamadas retornam None")

DEFAULT_TIMEOUT = 20  # segundos

_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="yf-safe")


def safe_yf_download(
    ticker: str,
    period: str,
    interval: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[pd.DataFrame]:
    """
    Wrapper de `yfinance.download` com timeout. Nunca lança exceção.

    Returns DataFrame com colunas flat (sem MultiIndex) ou None se:
      - yfinance não instalado
      - timeout expirou
      - resposta vazia
      - qualquer erro
    """
    if not YFINANCE_OK:
        return None

    try:
        future = _executor.submit(
            yf.download, ticker,
            period=period, interval=interval, progress=False,
        )
        df = future.result(timeout=timeout)
    except _FutureTimeout:
        log.warning(
            f"[yf-safe] {ticker} {period}/{interval} timeout após {timeout}s "
            "— Yahoo provavelmente throttling IP cloud, retornando None"
        )
        return None
    except Exception as e:
        log.debug(f"[yf-safe] {ticker} {period}/{interval} erro: {type(e).__name__}: {e}")
        return None

    if df is None or df.empty:
        return None

    # Flatten MultiIndex (yfinance v0.2+)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    return df
