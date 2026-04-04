"""
volatility_model.py — Modelo de Volatilidade de Ciclo
Trinity Trading | Bitcoin Cycle Engine

Detecta:
- Fase de volatilidade macro (dados semanais BTC)
- Expansão de ATR semanal
- Expansão de volume
- Bollinger Band Width

Fases:
  LOW:    vol comprimida → acumulação / antes de grande move
  MEDIUM: vol moderada → bull saudável, tendência estabelecida
  HIGH:   vol extrema → topo/fundo, risco operacional elevado
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False

# Limiares de ATR% semanal
ATR_LOW_THRESHOLD  = 3.5    # < 3.5% = baixa volatilidade
ATR_HIGH_THRESHOLD = 8.0    # > 8.0% = alta volatilidade


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calcula ATR (Average True Range)."""
    h = df["High"].values
    l = df["Low"].values
    c = df["Close"].values
    tr_list = [max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
               for i in range(1, len(c))]
    tr_s = pd.Series(tr_list)
    return tr_s.rolling(period).mean()


def _calc_bbwidth(df: pd.DataFrame, period: int = 20) -> Optional[float]:
    """Bollinger Band Width normalizado (%)."""
    try:
        cl  = df["Close"].dropna()
        if len(cl) < period:
            return None
        sma  = cl.rolling(period).mean()
        std  = cl.rolling(period).std()
        bbw  = ((sma + 2*std - (sma - 2*std)) / sma * 100).iloc[-1]
        return float(bbw) if not np.isnan(bbw) else None
    except Exception:
        return None


# ─── Run ─────────────────────────────────────────────────────────────────────

def run_volatility_model() -> dict:
    """
    Avalia a fase de volatilidade macro do BTC (dados semanais, 2 anos).

    Returns:
        {
            "volatility_phase":   "LOW" | "MEDIUM" | "HIGH",
            "volatility_score":   float 0-100,
            "atr_pct_weekly":     float,
            "atr_expansion":      bool,
            "volume_expansion":   bool,
            "bbwidth":            float | None,
            "vol_trend":          "EXPANDING" | "CONTRACTING" | "STABLE",
        }
    """
    _neutral = {
        "volatility_phase": "MEDIUM",
        "volatility_score": 50.0,
        "atr_pct_weekly":   5.0,
        "atr_expansion":    False,
        "volume_expansion": False,
        "bbwidth":          None,
        "vol_trend":        "STABLE",
    }

    if not YFINANCE_OK:
        return _neutral

    try:
        df = yf.download("BTC-USD", period="2y", interval="1wk", progress=False)
        if df.empty or len(df) < 20:
            raise ValueError("dados insuficientes")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        df = df.dropna(subset=["Close"])
    except Exception as e:
        log.debug(f"   Volatility model: yfinance falhou: {e}")
        return _neutral

    price_now = float(df["Close"].iloc[-1])

    # ── ATR% semanal ──────────────────────────────────────────────────────
    atr = _calc_atr(df, period=14)
    atr_now  = float(atr.iloc[-1])  if len(atr) >= 1  and not np.isnan(atr.iloc[-1])  else price_now * 0.05
    atr_prev = float(atr.iloc[-5])  if len(atr) >= 5  and not np.isnan(atr.iloc[-5])  else atr_now

    atr_pct       = (atr_now / price_now) * 100
    atr_expansion = atr_now > atr_prev * 1.3    # +30% de expansão vs 4 semanas atrás

    # ── Volume expansion ─────────────────────────────────────────────────
    vols = df["Volume"].dropna().values
    if len(vols) >= 8:
        vol_recent   = vols[-4:].mean()
        vol_base     = vols[-8:-4].mean()
        volume_expansion = bool(vol_recent > vol_base * 1.4)
    else:
        volume_expansion = False

    # ── Bollinger Band Width ──────────────────────────────────────────────
    bbwidth = _calc_bbwidth(df, period=20)

    # ── Tendência da volatilidade ─────────────────────────────────────────
    if len(atr) >= 8:
        atr_r = float(atr.iloc[-4:].mean())
        atr_o = float(atr.iloc[-8:-4].mean())
        if   atr_r > atr_o * 1.2:  vol_trend = "EXPANDING"
        elif atr_r < atr_o * 0.8:  vol_trend = "CONTRACTING"
        else:                       vol_trend = "STABLE"
    else:
        vol_trend = "STABLE"

    # ── Fase ─────────────────────────────────────────────────────────────
    if   atr_pct < ATR_LOW_THRESHOLD:  volatility_phase, base_score = "LOW",    70.0
    elif atr_pct < ATR_HIGH_THRESHOLD: volatility_phase, base_score = "MEDIUM", 55.0
    else:                               volatility_phase, base_score = "HIGH",   30.0

    # Ajuste pelo trend de volatilidade
    if vol_trend == "CONTRACTING" and volatility_phase != "LOW":
        base_score += 10   # comprimindo = setup melhorando
    elif vol_trend == "EXPANDING" and volatility_phase == "HIGH":
        base_score -= 10   # expandindo quando já alta = perigo adicional

    volatility_score = round(max(0.0, min(100.0, base_score)), 1)

    return {
        "volatility_phase":  volatility_phase,
        "volatility_score":  volatility_score,
        "atr_pct_weekly":    round(atr_pct, 2),
        "atr_expansion":     atr_expansion,
        "volume_expansion":  volume_expansion,
        "bbwidth":           round(bbwidth, 2) if bbwidth is not None else None,
        "vol_trend":         vol_trend,
    }
