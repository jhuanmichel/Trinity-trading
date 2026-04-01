"""
indicators/momentum.py — MÓDULO 3: Análise de Momentum
Detecta exaustão ou reversão do movimento.
Indicadores: RSI, Stochastic, CCI
"""
import pandas as pd
import ta


def analyze(df: pd.DataFrame) -> dict:
    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    # --- RSI ---
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
    rsi_val  = rsi.iloc[-1]
    rsi_prev = rsi.iloc[-2]

    # Divergência de RSI simples (preço sobe mas RSI cai = divergência bearish)
    price_higher = close.iloc[-1] > close.iloc[-5]
    rsi_lower    = rsi.iloc[-1] < rsi.iloc[-5]
    bearish_divergence = price_higher and rsi_lower

    price_lower  = close.iloc[-1] < close.iloc[-5]
    rsi_higher   = rsi.iloc[-1] > rsi.iloc[-5]
    bullish_divergence = price_lower and rsi_higher

    # --- Stochastic Oscillator ---
    stoch     = ta.momentum.StochasticOscillator(high, low, close, window=14, smooth_window=3)
    stoch_k   = stoch.stoch().iloc[-1]
    stoch_d   = stoch.stoch_signal().iloc[-1]
    stoch_cross_bull = (stoch.stoch().iloc[-1] > stoch.stoch_signal().iloc[-1]) and \
                       (stoch.stoch().iloc[-2] <= stoch.stoch_signal().iloc[-2])

    # --- CCI (Commodity Channel Index) ---
    cci     = ta.trend.CCIIndicator(high, low, close, window=20).cci()
    cci_val = cci.iloc[-1]

    # --- Score de momentum ---
    score = 50  # neutro

    # RSI
    if rsi_val < 30:
        score += 25    # sobrevenda → oportunidade de compra
        rsi_signal = "SOBREVENDA 🟢"
    elif rsi_val > 70:
        score -= 25    # sobrecompra → oportunidade de venda
        rsi_signal = "SOBRECOMPRA 🔴"
    elif 40 <= rsi_val <= 60:
        rsi_signal = "NEUTRO"
    elif rsi_val > 60:
        score += 10
        rsi_signal = "FORTE (bull)"
    else:
        score -= 10
        rsi_signal = "FRACO (bear)"

    # Divergências (sinal forte!)
    if bullish_divergence:
        score += 15
    if bearish_divergence:
        score -= 15

    # Stochastic
    if stoch_k < 20 and stoch_d < 20:
        score += 15
    elif stoch_k > 80 and stoch_d > 80:
        score -= 15
    if stoch_cross_bull and stoch_k < 30:
        score += 10  # cruzamento em zona de sobrevenda = sinal forte

    # CCI
    if cci_val < -100:
        score += 10   # sobrevenda no CCI
    elif cci_val > 100:
        score -= 10   # sobrecompra no CCI

    score = max(0, min(100, score))

    return {
        "score":              round(score),
        "rsi":                round(rsi_val, 2),
        "rsi_signal":         rsi_signal,
        "stoch_k":            round(stoch_k, 2),
        "stoch_d":            round(stoch_d, 2),
        "stoch_cross_bull":   stoch_cross_bull,
        "cci":                round(cci_val, 2),
        "bullish_divergence": bullish_divergence,
        "bearish_divergence": bearish_divergence,
        "summary": (
            f"RSI: {rsi_val:.1f} ({rsi_signal}) | "
            f"Stoch: K={stoch_k:.1f} D={stoch_d:.1f} | "
            f"CCI: {cci_val:.1f} | "
            f"{'⚡ DIVERGÊNCIA BULLISH' if bullish_divergence else ''}"
            f"{'⚡ DIVERGÊNCIA BEARISH' if bearish_divergence else ''}"
        ),
    }
