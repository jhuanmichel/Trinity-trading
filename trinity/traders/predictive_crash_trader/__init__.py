"""
trinity/traders/predictive_crash_trader — Preditor de Crash de Altcoins

Pipeline:
  1. AltcoinMarketScanner         → varre universo Binance Futures, filtra top 50
  2. LiquidityCollapseDetector    → colapso de liquidez, orderbook fino
  3. LeveragePressureDetector     → OI spike, funding extremo, cascade probability
  4. WhaleDumpDetector            → grandes vendas, CVD, pressão institucional
  5. VolatilityCompressionDetector→ squeeze BB, ATR comprimido, breakout iminente
  6. CascadePredictionModel       → clusters de stop, gaps de liquidez, força da cascata
  7. CrashScoringEngine           → score final ponderado [0-100]
  8. PredictiveCrashTrader        → orquestrador, loop de background, alertas Telegram
"""

from trinity.traders.predictive_crash_trader.predictive_crash_trader import (
    PredictiveCrashTrader,
    run_crash_scan,
)

__all__ = ["PredictiveCrashTrader", "run_crash_scan"]
