"""
indicators/trend.py — MÓDULO 2: Análise de Tendência
Determina a direção dominante do mercado.
Indicadores: EMA 20/50/200, MACD, Ichimoku Cloud
"""
import pandas as pd
import ta


def analyze(df: pd.DataFrame) -> dict:
    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    # --- EMAs ---
    ema20  = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    ema50  = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    ema200 = ta.trend.EMAIndicator(close, window=200).ema_indicator()

    e20  = ema20.iloc[-1]
    e50  = ema50.iloc[-1]
    e200 = ema200.iloc[-1]
    price = close.iloc[-1]

    # Cruzamento de EMA recente (últimos 5 candles)
    ema_cross_bull = (ema20.iloc[-1] > ema50.iloc[-1]) and (ema20.iloc[-6] <= ema50.iloc[-6])
    ema_cross_bear = (ema20.iloc[-1] < ema50.iloc[-1]) and (ema20.iloc[-6] >= ema50.iloc[-6])

    # --- MACD ---
    macd_ind    = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    macd_line   = macd_ind.macd().iloc[-1]
    signal_line = macd_ind.macd_signal().iloc[-1]
    histogram   = macd_ind.macd_diff().iloc[-1]

    # Cruzamento MACD recente
    macd_cross_bull = (macd_ind.macd().iloc[-1] > macd_ind.macd_signal().iloc[-1]) and \
                      (macd_ind.macd().iloc[-2] <= macd_ind.macd_signal().iloc[-2])
    macd_cross_bear = (macd_ind.macd().iloc[-1] < macd_ind.macd_signal().iloc[-1]) and \
                      (macd_ind.macd().iloc[-2] >= macd_ind.macd_signal().iloc[-2])

    # --- Ichimoku Cloud ---
    ich = ta.trend.IchimokuIndicator(high, low, window1=9, window2=26, window3=52)
    tenkan   = ich.ichimoku_conversion_line().iloc[-1]
    kijun    = ich.ichimoku_base_line().iloc[-1]
    span_a   = ich.ichimoku_a().iloc[-1]
    span_b   = ich.ichimoku_b().iloc[-1]
    cloud_top    = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)

    above_cloud  = price > cloud_top
    below_cloud  = price < cloud_bottom
    in_cloud     = cloud_bottom <= price <= cloud_top

    # --- Score de tendência ---
    score = 50  # neutro

    # EMA alignment
    if e20 > e50 > e200 and price > e20:
        score += 30    # tendência de alta perfeita
        ema_signal = "ALTA FORTE ✅"
    elif e20 < e50 < e200 and price < e20:
        score -= 30    # tendência de baixa perfeita
        ema_signal = "BAIXA FORTE 🔴"
    elif e20 > e50:
        score += 15
        ema_signal = "ALTA PARCIAL"
    elif e20 < e50:
        score -= 15
        ema_signal = "BAIXA PARCIAL"
    else:
        ema_signal = "NEUTRO"

    # MACD
    if macd_line > signal_line and histogram > 0:
        score += 10
    elif macd_line < signal_line and histogram < 0:
        score -= 10

    if macd_cross_bull:
        score += 10
    if macd_cross_bear:
        score -= 10

    # Ichimoku
    if above_cloud and tenkan > kijun:
        score += 10
    elif below_cloud and tenkan < kijun:
        score -= 10

    score = max(0, min(100, score))

    return {
        "score":          round(score),
        "ema_signal":     ema_signal,
        "ema20":          round(e20, 2),
        "ema50":          round(e50, 2),
        "ema200":         round(e200, 2),
        "macd":           round(macd_line, 4),
        "macd_signal":    round(signal_line, 4),
        "macd_histogram": round(histogram, 4),
        "macd_cross_bull": macd_cross_bull,
        "macd_cross_bear": macd_cross_bear,
        "ema_cross_bull": ema_cross_bull,
        "ema_cross_bear": ema_cross_bear,
        "ichimoku_above_cloud": above_cloud,
        "ichimoku_below_cloud": below_cloud,
        "ichimoku_in_cloud":    in_cloud,
        "summary": (
            f"Tendência: {ema_signal} | EMA: {e20:.0f}/{e50:.0f}/{e200:.0f} | "
            f"MACD: {'BULL' if macd_line > signal_line else 'BEAR'} "
            f"{'🔔 CRUZAMENTO!' if macd_cross_bull or macd_cross_bear else ''} | "
            f"Ichimoku: {'ACIMA' if above_cloud else 'ABAIXO' if below_cloud else 'DENTRO'} da nuvem"
        ),
    }
