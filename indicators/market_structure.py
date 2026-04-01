"""
indicators/market_structure.py — CAMADA 1: Market Structure (Smart Money)
Detecta: HH/HL/LH/LL, BOS, CHOCH, Liquidity Sweeps, Stop Hunts, Fake Breakouts, Equal Highs/Lows
"""
import pandas as pd
import numpy as np


def _find_swing_highs(df: pd.DataFrame, left: int = 5, right: int = 5) -> pd.Series:
    """Pivô de alta: candle com high maior que N candles à esq e N à dir."""
    highs = df["high"]
    mask = pd.Series(False, index=df.index)
    for i in range(left, len(highs) - right):
        window = highs.iloc[i - left: i + right + 1]
        if highs.iloc[i] == window.max() and highs.iloc[i] > highs.iloc[i - 1]:
            mask.iloc[i] = True
    return mask


def _find_swing_lows(df: pd.DataFrame, left: int = 5, right: int = 5) -> pd.Series:
    """Pivô de baixa: candle com low menor que N candles à esq e N à dir."""
    lows = df["low"]
    mask = pd.Series(False, index=df.index)
    for i in range(left, len(lows) - right):
        window = lows.iloc[i - left: i + right + 1]
        if lows.iloc[i] == window.min() and lows.iloc[i] < lows.iloc[i - 1]:
            mask.iloc[i] = True
    return mask


def analyze(df: pd.DataFrame, swing_lookback: int = 5) -> dict:
    """
    Analisa estrutura de mercado completa.

    Returns:
        dict com structure, bos_bull, bos_bear, choch, sweep_low, sweep_high,
        equal_highs, equal_lows, fake_breakouts e score 0-100.
    """
    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    # ── Swing points ─────────────────────────────────────────────────────
    sh_mask = _find_swing_highs(df, left=swing_lookback, right=swing_lookback)
    sl_mask = _find_swing_lows(df, left=swing_lookback, right=swing_lookback)

    swing_highs = high[sh_mask].dropna()
    swing_lows  = low[sl_mask].dropna()

    price = close.iloc[-1]

    # ── Estrutura: HH/HL vs LH/LL ────────────────────────────────────────
    structure = "INDEFINIDA"
    bos_bull  = False
    bos_bear  = False
    choch     = False

    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        last_sh = swing_highs.iloc[-1]
        prev_sh = swing_highs.iloc[-2]
        last_sl = swing_lows.iloc[-1]
        prev_sl = swing_lows.iloc[-2]

        hh = last_sh > prev_sh   # Higher High
        hl = last_sl > prev_sl   # Higher Low
        lh = last_sh < prev_sh   # Lower High
        ll = last_sl < prev_sl   # Lower Low

        if hh and hl:
            structure = "BULLISH"
        elif lh and ll:
            structure = "BEARISH"
        elif hh and ll:
            structure = "TRANSIÇÃO"
        else:
            structure = "LATERAL"

        # BOS — Break of Structure
        bos_bull = (close.iloc[-1] > last_sh) and (close.iloc[-2] <= last_sh)
        bos_bear = (close.iloc[-1] < last_sl) and (close.iloc[-2] >= last_sl)

        # CHOCH — Change of Character (BOS contrário à estrutura dominante)
        if structure == "BULLISH" and bos_bear:
            choch = True
            structure = "CHOCH BEARISH"
        elif structure == "BEARISH" and bos_bull:
            choch = True
            structure = "CHOCH BULLISH"

    # ── Liquidity Sweeps ──────────────────────────────────────────────────
    # Sweep abaixo: wick penetra swing low mas fecha acima (armadilha bearish → long)
    sweep_low  = False
    sweep_high = False

    if len(swing_lows) >= 1:
        ref_low = swing_lows.iloc[-1]
        for i in [-1, -2, -3]:
            if low.iloc[i] < ref_low and close.iloc[i] > ref_low:
                sweep_low = True
                break

    if len(swing_highs) >= 1:
        ref_high = swing_highs.iloc[-1]
        for i in [-1, -2, -3]:
            if high.iloc[i] > ref_high and close.iloc[i] < ref_high:
                sweep_high = True
                break

    # ── Equal Highs / Equal Lows (liquidez resting) ──────────────────────
    equal_highs = False
    equal_lows  = False
    tolerance   = 0.0015  # 0.15%

    if len(swing_highs) >= 2:
        diff = abs(swing_highs.iloc[-1] - swing_highs.iloc[-2]) / swing_highs.iloc[-2]
        equal_highs = diff < tolerance

    if len(swing_lows) >= 2:
        diff = abs(swing_lows.iloc[-1] - swing_lows.iloc[-2]) / swing_lows.iloc[-2]
        equal_lows = diff < tolerance

    # ── Fake Breakout / Inducement ────────────────────────────────────────
    fake_breakout_bull = False  # wick acima de swing high, fecha abaixo → short
    fake_breakout_bear = False  # wick abaixo de swing low, fecha acima → long

    if len(swing_highs) >= 1:
        ref = swing_highs.iloc[-1]
        if high.iloc[-1] > ref and close.iloc[-1] < ref:
            fake_breakout_bull = True

    if len(swing_lows) >= 1:
        ref = swing_lows.iloc[-1]
        if low.iloc[-1] < ref and close.iloc[-1] > ref:
            fake_breakout_bear = True

    # ── Score institucional ───────────────────────────────────────────────
    score = 50

    if structure == "BULLISH":
        score += 15
    elif structure == "BEARISH":
        score -= 15
    elif structure == "CHOCH BULLISH":
        score += 25
    elif structure == "CHOCH BEARISH":
        score -= 25

    if bos_bull:
        score += 12
    if bos_bear:
        score -= 12

    # Sweep abaixo + bullish = confluência institucional
    if sweep_low:
        score += 12
    if sweep_high:
        score -= 12

    # Fake breakout para baixo = sinal de long
    if fake_breakout_bear:
        score += 8
    # Fake breakout para cima = sinal de short
    if fake_breakout_bull:
        score -= 8

    # Equal lows = liquidez abaixo → provavelmente varrida → long
    if equal_lows:
        score += 5
    # Equal highs = liquidez acima → provavelmente varrida → short
    if equal_highs:
        score -= 5

    score = max(0, min(100, score))

    if score >= 65:
        bias = "BULLISH"
    elif score <= 35:
        bias = "BEARISH"
    else:
        bias = "NEUTRO"

    return {
        "score":               round(score),
        "bias":                bias,
        "structure":           structure,
        "bos_bull":            bos_bull,
        "bos_bear":            bos_bear,
        "choch":               choch,
        "sweep_low":           sweep_low,
        "sweep_high":          sweep_high,
        "equal_highs":         equal_highs,
        "equal_lows":          equal_lows,
        "fake_breakout_bull":  fake_breakout_bull,
        "fake_breakout_bear":  fake_breakout_bear,
        "last_swing_high":     round(float(swing_highs.iloc[-1]), 2) if len(swing_highs) >= 1 else None,
        "last_swing_low":      round(float(swing_lows.iloc[-1]), 2)  if len(swing_lows) >= 1  else None,
        "summary": (
            f"Estrutura: {structure} | BOS: {'BULL 🟢' if bos_bull else 'BEAR 🔴' if bos_bear else '—'} | "
            f"CHOCH: {'⚡SIM' if choch else 'NÃO'} | "
            f"Sweep: {'↓abaixo ' if sweep_low else ''}{'↑acima' if sweep_high else '—'} | "
            f"EQH: {'SIM ⚠️' if equal_highs else 'não'} | EQL: {'SIM ⚠️' if equal_lows else 'não'}"
        ),
    }
