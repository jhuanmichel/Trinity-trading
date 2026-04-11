"""
trinity/traders/predictive_pump_trader — Preditor de Pump Antecipado (Cap. 20)

NÃO detecta pumps que já aconteceram.
PREDIZ pumps ANTES de acontecerem.

Pipeline:
  1. AltcoinMarketScanner (reutilizado do crash trader)
  2. WhaleAccumulationDetector    → acumulação institucional oculta
  3. ShortSqueezeDetector         → shorts sobrecarregados + funding negativo
  4. LiquidityGravityDetector     → clusters de liquidez acima do preço
  5. BreakoutPressureDetector     → compressão + volume crescendo
  6. PumpPredictionModel          → padrões de higher lows + absorção
  7. PumpScoringEngine            → score ponderado [0-100]
  8. PredictivePumpTrader         → orquestrador, loop 30s, Telegram, dashboard
"""

from trinity.traders.predictive_pump_trader.predictive_pump_trader import (
    PredictivePumpTrader,
    run_pump_scan,
    run_pump_cycle,
)

__all__ = ["PredictivePumpTrader", "run_pump_scan", "run_pump_cycle"]
