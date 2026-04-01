"""
indicators/volume.py — MÓDULO 4: Análise de Volume
Volume confirma ou invalida movimentos de preço.
Indicadores: OBV, VWAP, Chaikin Money Flow
"""
import pandas as pd
import ta
import numpy as np


def analyze(df: pd.DataFrame) -> dict:
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    # --- OBV (On-Balance Volume) ---
    obv     = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    obv_val = obv.iloc[-1]
    obv_ema = obv.ewm(span=20).mean().iloc[-1]   # EMA do OBV
    obv_trend_bull = obv_val > obv_ema            # OBV acima da EMA = pressão compradora

    # --- VWAP (Volume Weighted Average Price) ---
    # VWAP diário (reinicia a cada sessão — aqui usamos janela rolante de 20 candles)
    typical_price = (high + low + close) / 3
    vwap = (typical_price * volume).rolling(20).sum() / volume.rolling(20).sum()
    vwap_val = vwap.iloc[-1]
    price_above_vwap = close.iloc[-1] > vwap_val

    # --- CMF (Chaikin Money Flow) ---
    cmf     = ta.volume.ChaikinMoneyFlowIndicator(high, low, close, volume, window=20).chaikin_money_flow()
    cmf_val = cmf.iloc[-1]

    # --- Volume relativo (vs média) ---
    vol_ma    = volume.rolling(20).mean().iloc[-1]
    vol_now   = volume.iloc[-1]
    vol_ratio = vol_now / vol_ma if vol_ma > 0 else 1.0
    high_volume = vol_ratio > 1.5   # volume 50% acima da média = confirmação

    # --- CVD (Cumulative Volume Delta) ---
    # Delta por candle: bullish candle = +volume, bearish = -volume
    delta = volume.where(close >= df["open"], -volume)
    cvd   = delta.cumsum()
    cvd_val      = cvd.iloc[-1]
    cvd_5        = cvd.iloc[-5]
    cvd_trending_up   = cvd_val > cvd_5   # CVD subindo = pressão compradora
    cvd_trending_down = cvd_val < cvd_5   # CVD caindo  = pressão vendedora

    # Delta do último candle
    last_delta = float(delta.iloc[-1])
    delta_bull = last_delta > 0

    # Últimos 3 candles: preço sobe + volume sobe?
    price_up   = close.iloc[-1] > close.iloc[-3]
    vol_up     = volume.iloc[-1] > volume.iloc[-3]
    healthy_move  = price_up and vol_up
    trap_signal   = price_up and not vol_up     # preço sobe mas volume cai = armadilha

    # --- Score de volume ---
    score = 50

    if healthy_move:
        score += 20
    elif trap_signal:
        score -= 20

    if obv_trend_bull:
        score += 10
    else:
        score -= 10

    if price_above_vwap:
        score += 10
    else:
        score -= 10

    if cmf_val > 0.1:
        score += 10   # fluxo de dinheiro positivo
    elif cmf_val < -0.1:
        score -= 10

    if high_volume:
        score += 10   # bônus por volume alto (confirmação de rompimento)

    # CVD
    if cvd_trending_up:
        score += 10
    elif cvd_trending_down:
        score -= 10

    score = max(0, min(100, score))

    return {
        "score":              round(score),
        "obv":                round(obv_val, 0),
        "obv_bull":           obv_trend_bull,
        "vwap":               round(vwap_val, 2),
        "price_above_vwap":   price_above_vwap,
        "cmf":                round(cmf_val, 4),
        "vol_ratio":          round(vol_ratio, 2),
        "high_volume":        high_volume,
        "healthy_move":       healthy_move,
        "trap_signal":        trap_signal,
        "cvd":                round(cvd_val, 0),
        "cvd_trending_up":    cvd_trending_up,
        "cvd_trending_down":  cvd_trending_down,
        "last_delta":         round(last_delta, 0),
        "delta_bull":         delta_bull,
        "summary": (
            f"OBV: {'BULL 📈' if obv_trend_bull else 'BEAR 📉'} | "
            f"CVD: {'↑subindo' if cvd_trending_up else '↓caindo'} | "
            f"VWAP: ${vwap_val:,.2f} ({'ACIMA' if price_above_vwap else 'ABAIXO'}) | "
            f"CMF: {cmf_val:.3f} | Vol: {vol_ratio:.1f}x média | "
            f"{'✅ Movimento saudável' if healthy_move else '⚠️ ARMADILHA' if trap_signal else ''}"
        ),
    }
