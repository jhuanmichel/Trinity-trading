"""
trinity/traders/pump_radar — Radar de Pump Antecipado (Cap. 20)

Wrapper para o Predictive Pump Trader (implementação definitiva em
trinity/traders/predictive_pump_trader/).

Detecta ANTES que o pump aconteça:
  1. WhaleAccumulationDetector  → acumulação institucional oculta
  2. ShortSqueezeDetector       → shorts sobrecarregados + funding negativo
  3. LiquidityGravityDetector   → clusters de liquidez acima do preço
  4. BreakoutPressureDetector   → compressão + volume crescendo
  5. PumpScoringEngine          → score final ponderado [0-100]
"""

from trinity.traders.predictive_pump_trader import (
    PredictivePumpTrader,
    run_pump_scan,
    run_pump_cycle,
)

# Alias para compatibilidade com a interface original (PumpRadar → PredictivePumpTrader)
PumpRadar = PredictivePumpTrader

__all__ = ["PumpRadar", "PredictivePumpTrader", "run_pump_scan", "run_pump_cycle"]
