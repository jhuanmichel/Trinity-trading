"""
trinity/traders — Módulo de Traders Especializados do Trinity System

Traders:
  predictive_crash_trader — Predição antecipada de crashes em altcoins
"""

from trinity.traders.predictive_crash_trader.predictive_crash_trader import (
    PredictiveCrashTrader,
    run_crash_scan,
)

__all__ = ["PredictiveCrashTrader", "run_crash_scan"]
