"""
onchain_model.py — Modelo On-Chain Macro
Trinity Trading | Bitcoin Cycle Engine

Métricas:
  MVRV  — Market Value to Realized Value
  NUPL  — Net Unrealized Profit/Loss
  SOPR  — Spent Output Profit Ratio (aproximado)
  Realized Price — VWAP anual via yfinance
  Smart Money Behavior — ACCUMULATING | DISTRIBUTING | NEUTRAL

Abordagem:
  1. Se dados externos do indicators/onchain.py disponíveis → usa como proxy
  2. Fallback: aproximação via VWAP anual (yfinance)

Interpretação MVRV:
  > 3.5  → DISTRIBUTING (topo de ciclo)
  2.0-3.5 → LATE_BULL / início de distribuição
  1.0-2.0 → EARLY / MID BULL — zona saudável
  0.8-1.0 → ACCUMULATION
  < 0.8  → CAPITULATION (comprar com agressividade)

NUPL = 1 - (1 / MVRV)
  > 0.75 → EUPHORIA
  0.50-0.75 → BELIEF
  0.25-0.50 → OPTIMISM
  0-0.25 → HOPE
  < 0  → CAPITULATION
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

from ._yf_safe import safe_yf_download


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _approximate_realized_price() -> Optional[float]:
    """
    Approximates Realized Price via VWAP anual (365 dias).
    Realized Price ≈ preço médio pago por todos os holders.
    """
    df = safe_yf_download("BTC-USD", period="2y", interval="1d")
    if df is None:
        return None
    try:
        df_1y   = df.tail(365).copy()
        typical = (df_1y["High"] + df_1y["Low"] + df_1y["Close"]) / 3
        vol     = df_1y["Volume"]
        if vol.sum() > 0:
            vwap = float((typical * vol).sum() / vol.sum())
        else:
            vwap = float(df_1y["Close"].mean())
        return vwap
    except Exception as e:
        log.debug(f"   Onchain: realized price falhou: {e}")
        return None


def _get_current_price() -> Optional[float]:
    """Obtém preço atual do BTC via yfinance (com timeout)."""
    df = safe_yf_download("BTC-USD", period="5d", interval="1d")
    if df is None:
        return None
    try:
        return float(df["Close"].dropna().iloc[-1])
    except Exception:
        return None


def _mvrv_to_score_and_behavior(mvrv: float) -> tuple:
    """
    Converte MVRV em (score 0-100, smart_money_behavior).
    """
    if mvrv < 0.7:
        return (92.0, "ACCUMULATING")   # capitulação extrema
    elif mvrv < 0.9:
        return (82.0, "ACCUMULATING")   # abaixo do custo realizado
    elif mvrv < 1.3:
        return (72.0, "ACCUMULATING")   # early accumulation / recovery
    elif mvrv < 1.7:
        return (65.0, "ACCUMULATING")   # early bull
    elif mvrv < 2.2:
        return (58.0, "NEUTRAL")        # mid bull saudável
    elif mvrv < 2.7:
        return (48.0, "NEUTRAL")        # mid-late bull
    elif mvrv < 3.2:
        return (32.0, "DISTRIBUTING")   # late bull / distribuição começa
    elif mvrv < 3.7:
        return (18.0, "DISTRIBUTING")   # distribuição intensa
    else:
        return (8.0,  "DISTRIBUTING")   # topo extremo


def _nupl_phase(nupl: float) -> str:
    """Classifica fase NUPL."""
    if nupl < 0:
        return "CAPITULATION"
    elif nupl < 0.25:
        return "HOPE"
    elif nupl < 0.50:
        return "OPTIMISM"
    elif nupl < 0.75:
        return "BELIEF"
    return "EUPHORIA"


def _score_from_fg(fg: float) -> tuple:
    """Proxy usando Fear & Greed Index quando MVRV não disponível."""
    if fg < 20:
        return (88.0, "ACCUMULATING")
    elif fg < 35:
        return (72.0, "ACCUMULATING")
    elif fg < 50:
        return (62.0, "ACCUMULATING")
    elif fg < 65:
        return (55.0, "NEUTRAL")
    elif fg < 80:
        return (38.0, "NEUTRAL")
    else:
        return (22.0, "DISTRIBUTING")


# ─── Run ─────────────────────────────────────────────────────────────────────

def run_onchain_model(external_onchain: Optional[dict] = None) -> dict:
    """
    Calcula métricas on-chain macro para classificação de ciclo.

    Args:
        external_onchain: dict do indicators/onchain.py (opcional)
                          Usa fg_value como proxy de sentimento se disponível.

    Returns:
        {
            "onchain_strength":     float 0-100,
            "smart_money_behavior": "ACCUMULATING" | "DISTRIBUTING" | "NEUTRAL",
            "mvrv":                 float | None,
            "nupl":                 float | None,
            "nupl_phase":           str,
            "realized_price":       float | None,
            "current_price":        float | None,
            "data_source":          "mvrv_approximated" | "fg_proxy" | "unavailable",
        }
    """
    # ── Proxy via Fear & Greed se disponível ──────────────────────────────
    if external_onchain:
        fg = external_onchain.get("fg_value", None)
        if fg is not None:
            try:
                fg_val = float(fg)
                score, behavior = _score_from_fg(fg_val)
                return {
                    "onchain_strength":     round(score, 1),
                    "smart_money_behavior": behavior,
                    "mvrv":                 None,
                    "nupl":                 None,
                    "nupl_phase":           "UNKNOWN",
                    "realized_price":       None,
                    "current_price":        None,
                    "data_source":          "fg_proxy",
                }
            except Exception:
                pass

    # ── Aproximação via VWAP (Realized Price) ─────────────────────────────
    realized_price = _approximate_realized_price()
    current_price  = _get_current_price()

    if realized_price is None or current_price is None or realized_price <= 0:
        return {
            "onchain_strength":     50.0,
            "smart_money_behavior": "NEUTRAL",
            "mvrv":                 None,
            "nupl":                 None,
            "nupl_phase":           "UNKNOWN",
            "realized_price":       None,
            "current_price":        None,
            "data_source":          "unavailable",
        }

    mvrv         = round(current_price / realized_price, 3)
    nupl         = round(1.0 - (1.0 / mvrv), 3) if mvrv > 0 else -0.5
    nupl_ph      = _nupl_phase(nupl)
    score, beh   = _mvrv_to_score_and_behavior(mvrv)

    return {
        "onchain_strength":     round(score, 1),
        "smart_money_behavior": beh,
        "mvrv":                 mvrv,
        "nupl":                 nupl,
        "nupl_phase":           nupl_ph,
        "realized_price":       round(realized_price, 2),
        "current_price":        round(current_price, 2),
        "data_source":          "mvrv_approximated",
    }
