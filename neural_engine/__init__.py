"""
neural_engine — Trinity Neural Intelligence Engine v2
Cap. 17 | Sistema neural multi-modelo adaptativo para inteligência institucional.

Arquitetura:
  feature_engine.py    → extração e normalização de features
  lstm_model.py        → padrões temporais (sequências)
  transformer_model.py → atenção multi-cabeça multi-timeframe
  cnn_model.py         → detecção de padrões gráficos (1D conv)
  mlp_model.py         → fusão do Trinity Score (tabular)
  ensemble_model.py    → combinador ponderado dos 5 modelos
  regime_detector.py   → detecção de regime de mercado
  bias_balancer.py     → motor de viés market-neutral
  retraining_pipeline.py → retreinamento adaptativo periódico
  neural_engine.py     → orquestrador principal (entry point)

Saída padrão:
  {
    "bull_probability":              0-100,
    "bear_probability":              0-100,
    "sideways_probability":          0-100,
    "volatility_expansion_probability": 0-100,
    "manipulation_probability":      0-100,
    "confidence_score":              0-100,
    "market_regime":                 TRENDING|RANGING|REVERSAL|ACCUMULATION|...,
    "neural_score":                  0-100,
    "neural_bias":                   LONG|SHORT|NEUTRAL,
    "rare_neural_setup":             bool,
    "rare_neural_type":              str|None,
  }
"""

from .neural_engine import run_neural_analysis, NeuralEngine

__all__ = ["run_neural_analysis", "NeuralEngine"]
