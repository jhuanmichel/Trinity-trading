"""
trend_model.py — Modelo de Tendência de Ciclo
Trinity Trading | Bitcoin Cycle Engine

Detecta:
- Monthly/Weekly trend direction
- Posição relativa à 200W MA e 50W MA
- Higher Highs / Lower Highs (estrutura macro)
- Drawdown % desde ATH (janela 4 anos)
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

from ._yf_safe import safe_yf_download


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _download(period: str, interval: str) -> Optional[pd.DataFrame]:
    """Baixa dados históricos do BTC via yfinance (com timeout)."""
    df = safe_yf_download("BTC-USD", period=period, interval=interval)
    if df is None:
        return None
    df = df.dropna(subset=["Close"])
    return df if len(df) >= 4 else None


def _trend_direction(df: pd.DataFrame) -> str:
    """Direção de tendência por regressão linear simples."""
    closes = df["Close"].dropna().values[-12:]
    if len(closes) < 4:
        return "SIDEWAYS"
    x = np.arange(len(closes), dtype=float)
    slope = np.polyfit(x, closes, 1)[0]
    norm_slope = slope / closes[-1] * 100   # slope em % do preço atual
    if norm_slope > 0.5:
        return "UP"
    elif norm_slope < -0.5:
        return "DOWN"
    return "SIDEWAYS"


def _detect_macro_structure(df: pd.DataFrame, lookback: int = 20) -> str:
    """
    Detecta estrutura macro de HH/HL vs LH/LL.
    Retorna 'HH_HL' | 'LH_LL' | 'MIXED'.
    """
    if df is None or len(df) < lookback:
        return "MIXED"
    highs = df["High"].dropna().values[-lookback:]
    lows  = df["Low"].dropna().values[-lookback:]
    mid   = lookback // 2
    hh = highs[mid:].max() > highs[:mid].max()
    hl = lows[mid:].min()  > lows[:mid].min()
    lh = highs[mid:].max() < highs[:mid].max()
    ll = lows[mid:].min()  < lows[:mid].min()
    if hh and hl:
        return "HH_HL"
    if lh and ll:
        return "LH_LL"
    return "MIXED"


# ─── Run ─────────────────────────────────────────────────────────────────────

def run_trend_model() -> dict:
    """
    Calcula o modelo de tendência macro do Bitcoin (dados semanais/mensais).

    Returns:
        {
            "trend_strength":  0-100,
            "trend_direction": "UP" | "DOWN" | "SIDEWAYS",
            "above_200w_ma":   bool | None,
            "above_50w_ma":    bool | None,
            "ma_200w":         float | None,
            "ma_50w":          float | None,
            "current_price":   float | None,
            "drawdown_pct":    float,
            "drawdown_score":  0-100,
            "weekly_trend":    "UP" | "DOWN" | "SIDEWAYS",
            "monthly_trend":   "UP" | "DOWN" | "SIDEWAYS",
            "macro_structure": "HH_HL" | "LH_LL" | "MIXED",
        }
    """
    df_w = _download("5y", "1wk")
    df_m = _download("5y", "1mo")

    # Fallback neutro
    if df_w is None or len(df_w) < 20:
        log.debug("   Trend model: dados semanais insuficientes — retornando neutro")
        return {
            "trend_strength":  50.0,
            "trend_direction": "SIDEWAYS",
            "above_200w_ma":   None,
            "above_50w_ma":    None,
            "ma_200w":         None,
            "ma_50w":          None,
            "current_price":   None,
            "drawdown_pct":    0.0,
            "drawdown_score":  50.0,
            "weekly_trend":    "SIDEWAYS",
            "monthly_trend":   "SIDEWAYS",
            "macro_structure": "MIXED",
        }

    closes_w   = df_w["Close"].dropna()
    price_now  = float(closes_w.iloc[-1])

    # ── 200W MA e 50W MA ────────────────────────────────────────────────────
    ma200w = float(closes_w.rolling(200).mean().iloc[-1]) if len(closes_w) >= 200 else float(closes_w.mean())
    ma50w  = float(closes_w.rolling(50).mean().iloc[-1])  if len(closes_w) >= 50  else float(closes_w.mean())
    above_200w = price_now > ma200w
    above_50w  = price_now > ma50w

    # ── Drawdown desde ATH (4 anos = ~208 semanas) ─────────────────────────
    ath_window  = min(208, len(closes_w))
    ath         = float(closes_w.iloc[-ath_window:].max())
    drawdown_pct = round(((price_now - ath) / ath * 100) if ath > 0 else 0.0, 2)
    # 0% drawdown = score 100; -80% drawdown = score 0
    drawdown_score = round(max(0.0, min(100.0, 100 + drawdown_pct * 1.25)), 1)

    # ── Direções ─────────────────────────────────────────────────────────────
    weekly_trend  = _trend_direction(df_w)
    monthly_trend = _trend_direction(df_m) if df_m is not None else weekly_trend

    # ── Estrutura macro (últimas 20 semanas) ──────────────────────────────────
    macro_structure = _detect_macro_structure(df_w, lookback=20)

    # ── Trend Strength 0-100 ──────────────────────────────────────────────────
    # 1. Posição relativa às MAs          (0-40 pts)
    # 2. Direção semanal + mensal         (0-30 pts)
    # 3. Drawdown recovery                (0-20 pts)
    # 4. Estrutura macro HH/HL vs LH/LL   (0-10 pts)

    # MA score
    dist_200w_pct = (price_now - ma200w) / ma200w * 100
    if above_200w:
        ma_score = 25.0 + min(15.0, dist_200w_pct)
    else:
        ma_score = max(0.0, 25.0 + dist_200w_pct * 2)

    # Direção score
    dir_map  = {"UP": 15.0, "SIDEWAYS": 7.5, "DOWN": 0.0}
    dir_score = dir_map.get(weekly_trend, 7.5) + dir_map.get(monthly_trend, 7.5)

    # Drawdown contribution
    dd_contrib = drawdown_score * 0.20

    # Estrutura score
    struct_score = {"HH_HL": 10.0, "MIXED": 5.0, "LH_LL": 0.0}.get(macro_structure, 5.0)

    trend_strength = round(min(100.0, max(0.0,
        ma_score + dir_score + dd_contrib + struct_score
    )), 1)

    # Direção consolidada
    if weekly_trend == "UP" and monthly_trend in ("UP", "SIDEWAYS") and above_200w:
        trend_direction = "UP"
    elif weekly_trend == "DOWN" and monthly_trend in ("DOWN", "SIDEWAYS") and not above_200w:
        trend_direction = "DOWN"
    else:
        trend_direction = "SIDEWAYS"

    return {
        "trend_strength":  trend_strength,
        "trend_direction": trend_direction,
        "above_200w_ma":   above_200w,
        "above_50w_ma":    above_50w,
        "ma_200w":         round(ma200w, 2),
        "ma_50w":          round(ma50w, 2),
        "current_price":   round(price_now, 2),
        "drawdown_pct":    drawdown_pct,
        "drawdown_score":  drawdown_score,
        "weekly_trend":    weekly_trend,
        "monthly_trend":   monthly_trend,
        "macro_structure": macro_structure,
    }
