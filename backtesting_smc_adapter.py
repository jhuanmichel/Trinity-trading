"""
backtesting_smc_adapter.py — Adapter SMC offline para backtesting

Fornece cálculos SMC sobre DataFrames locais sem nenhuma chamada externa (API/CCXT).
Usado pelo BacktestingEngine para simular sinais sobre dados históricos.

Funções públicas:
    resample_to_higher_tfs(df_1h)       → dict com "1h", "4h", "1d" DataFrames
    calculate_score_offline(df_window)  → dict com smc_score, direction, confluences
    detect_structure_offline(df_window) → dict com structure, bias, bos_bull, etc.
    calculate_entry_stop_tp(df, direction, atr_mult, tp1_r, tp2_r, tp3_r)
                                        → dict com entry, stop, tp1, tp2, tp3
"""
import logging
import sys
import os
from typing import Optional

import numpy as np
import pandas as pd

# Garante que o root do projeto está no sys.path para importar smart_money_engine
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from smart_money_engine import SmartMoneyEngine, SMC_WEIGHTS

log = logging.getLogger(__name__)


# ── Resample ─────────────────────────────────────────────────────────────────

def resample_to_higher_tfs(df_1h: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Recria os DataFrames 4h e 1d a partir do 1h por resample pandas.

    Args:
        df_1h: DataFrame OHLCV com DatetimeIndex UTC (granularidade 1h)

    Returns:
        dict com keys "15m", "1h", "4h", "1d" (15m é alias de 1h para compatibilidade
        com BacktestSME que espera os 4 timeframes).
    """
    if df_1h.empty:
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return {"15m": empty, "1h": empty, "4h": empty, "1d": empty}

    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}

    df_4h = df_1h.resample("4h", origin="epoch", closed="left", label="left").agg(agg).dropna()
    df_1d = df_1h.resample("1D", origin="epoch", closed="left", label="left").agg(agg).dropna()

    return {
        "15m": df_1h,   # proxy: sem dados 15m reais no histórico 1h
        "1h":  df_1h,
        "4h":  df_4h,
        "1d":  df_1d,
    }


# ── Score offline ─────────────────────────────────────────────────────────────

def calculate_score_offline(df_window: pd.DataFrame) -> dict:
    """
    Calcula score SMC sobre uma janela de DataFrame OHLCV sem chamadas externas.

    Usa SmartMoneyEngine.calculate_score() que é totalmente local — não faz API calls.

    Args:
        df_window: DataFrame OHLCV com pelo menos 30 candles

    Returns:
        dict com: smc_score (0-100), direction ("LONG"/"SHORT"/"AGUARDANDO"),
                  confluences (int), valid (bool), bias (str), layer_scores (dict)
    """
    if df_window is None or len(df_window) < 20:
        return {
            "smc_score":    50.0,
            "direction":    "AGUARDANDO",
            "confluences":  0,
            "valid":        False,
            "bias":         "NEUTRO",
            "layer_scores": {},
        }
    try:
        eng = SmartMoneyEngine(df_window.copy(), "BTCUSDT")
        return eng.calculate_score()
    except Exception as e:
        log.debug(f"[SMCAdapter] calculate_score_offline error: {e}")
        return {
            "smc_score":    50.0,
            "direction":    "AGUARDANDO",
            "confluences":  0,
            "valid":        False,
            "bias":         "NEUTRO",
            "layer_scores": {},
        }


# ── Structure offline ─────────────────────────────────────────────────────────

def detect_structure_offline(df_window: pd.DataFrame) -> dict:
    """
    Detecta estrutura de mercado (BOS, CHoCH, HH/HL/LH/LL) sobre janela local.

    Args:
        df_window: DataFrame OHLCV com pelo menos 20 candles

    Returns:
        dict com: structure, bias, bos_bull, bos_bear, choch, sweep_low, sweep_high,
                  equal_highs, equal_lows, score
    """
    if df_window is None or len(df_window) < 20:
        return {
            "structure":   "INDEFINIDA",
            "bias":        "NEUTRO",
            "bos_bull":    False,
            "bos_bear":    False,
            "choch":       False,
            "sweep_low":   False,
            "sweep_high":  False,
            "equal_highs": False,
            "equal_lows":  False,
            "score":       50,
        }
    try:
        eng = SmartMoneyEngine(df_window.copy(), "BTCUSDT")
        return eng.detect_market_structure()
    except Exception as e:
        log.debug(f"[SMCAdapter] detect_structure_offline error: {e}")
        return {
            "structure": "INDEFINIDA", "bias": "NEUTRO",
            "bos_bull": False, "bos_bear": False, "choch": False,
            "sweep_low": False, "sweep_high": False,
            "equal_highs": False, "equal_lows": False, "score": 50,
        }


# ── Entry / Stop / TP ────────────────────────────────────────────────────────

def calculate_entry_stop_tp(
    df_window: pd.DataFrame,
    direction: str,
    atr_mult: float = 2.0,
    tp1_ratio: float = 1.5,
    tp2_ratio: float = 3.0,
    tp3_ratio: float = 5.0,
) -> dict:
    """
    Calcula níveis de entrada, stop e alvos baseados em ATR.

    Fórmula:
        LONG:  stop = close - ATR*atr_mult
               tp_n = close + ATR*atr_mult*tp_n_ratio
        SHORT: stop = close + ATR*atr_mult
               tp_n = close - ATR*atr_mult*tp_n_ratio

    Args:
        df_window:  DataFrame OHLCV (mínimo 20 candles)
        direction:  "LONG" ou "SHORT"
        atr_mult:   multiplicador ATR para stop (default 2.0)
        tp1_ratio:  ratio TP1 relativo ao risco (default 1.5 → 1.5R)
        tp2_ratio:  ratio TP2 (default 3.0 → 3R)
        tp3_ratio:  ratio TP3 (default 5.0 → 5R)

    Returns:
        dict com: entry, stop, tp1, tp2, tp3, risk, atr
    """
    if df_window is None or len(df_window) < 15:
        return {"entry": 0.0, "stop": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
                "risk": 0.0, "atr": 0.0}
    try:
        close = float(df_window["close"].iloc[-1])
        atr   = _calc_atr(df_window, period=14)

        risk  = atr * atr_mult
        entry = close

        if direction == "LONG":
            stop = entry - risk
            tp1  = entry + risk * tp1_ratio
            tp2  = entry + risk * tp2_ratio
            tp3  = entry + risk * tp3_ratio
        elif direction == "SHORT":
            stop = entry + risk
            tp1  = entry - risk * tp1_ratio
            tp2  = entry - risk * tp2_ratio
            tp3  = entry - risk * tp3_ratio
        else:
            stop = tp1 = tp2 = tp3 = entry

        return {
            "entry": round(entry, 4),
            "stop":  round(stop,  4),
            "tp1":   round(tp1,   4),
            "tp2":   round(tp2,   4),
            "tp3":   round(tp3,   4),
            "risk":  round(risk,  4),
            "atr":   round(atr,   4),
        }
    except Exception as e:
        log.debug(f"[SMCAdapter] calculate_entry_stop_tp error: {e}")
        return {"entry": 0.0, "stop": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
                "risk": 0.0, "atr": 0.0}


# ── Private helpers ───────────────────────────────────────────────────────────

def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """ATR(14) do DataFrame."""
    h = df["high"]
    l = df["low"]
    c = df["close"]
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    atr_val = tr.rolling(period).mean().iloc[-1]
    if pd.isna(atr_val) or atr_val <= 0:
        return float(c.iloc[-1]) * 0.005   # fallback: 0.5% do preço
    return float(atr_val)
