"""
indicators/regime.py — MÓDULO 1: Identificação de Regime de Mercado
Determina se o mercado está em TENDÊNCIA ou LATERAL (range).
Indicadores: ADX, Bollinger Band Width, ATR
"""
import pandas as pd
import ta


def analyze(df: pd.DataFrame) -> dict:
    """
    Analisa o regime de mercado.

    Returns:
        dict com regime, scores e valores dos indicadores
    """
    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    # --- ADX (Average Directional Index) ---
    adx_indicator = ta.trend.ADXIndicator(high, low, close, window=14)
    adx    = adx_indicator.adx().iloc[-1]
    adx_p  = adx_indicator.adx_pos().iloc[-1]   # +DI (força compradora)
    adx_n  = adx_indicator.adx_neg().iloc[-1]   # -DI (força vendedora)

    # --- Bollinger Band Width ---
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_width   = bb.bollinger_wband().iloc[-1]       # largura das bandas
    bb_width_ma = bb.bollinger_wband().rolling(20).mean().iloc[-1]
    squeeze    = bb_width < bb_width_ma * 0.8        # squeeze = bandas muito estreitas

    # --- ATR ---
    atr     = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1]
    atr_pct = (atr / close.iloc[-1]) * 100           # ATR como % do preço

    # --- Classificação do regime ---
    if adx < 20:
        regime = "LATERAL"
        regime_score = 20          # baixo score para tendência
    elif 20 <= adx < 30:
        regime = "INÍCIO DE TENDÊNCIA"
        regime_score = 55
    else:
        regime = "TENDÊNCIA FORTE"
        regime_score = 85

    # Squeeze adiciona pressão — grande movimento vindo
    if squeeze:
        regime += " + SQUEEZE ⚡"

    # Direção da tendência pelo ADX
    direction = "ALTA" if adx_p > adx_n else "BAIXA"

    return {
        "regime":       regime,
        "direction":    direction,
        "score":        round(regime_score),
        "squeeze":      squeeze,
        "adx":          round(adx, 2),
        "adx_plus_di":  round(adx_p, 2),
        "adx_minus_di": round(adx_n, 2),
        "bb_width":     round(bb_width, 4),
        "atr":          round(atr, 2),
        "atr_pct":      round(atr_pct, 2),
        "summary": (
            f"ADX {adx:.1f} → {regime} | Direção: {direction} | "
            f"ATR: {atr_pct:.2f}% | {'🔥 SQUEEZE DETECTADO' if squeeze else 'Sem squeeze'}"
        ),
    }
