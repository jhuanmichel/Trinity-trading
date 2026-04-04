"""
trinity/core — Núcleo de Inteligência do Trinity Trading System

Pipeline de execução:
  1. EngineOrchestrator  → executa todos os 10 engines, retorna EngineResult padronizado
  2. SignalConsensus      → voto direcional BULLISH/BEARISH/NEUTRAL entre engines
  3. ConflictDetector     → detecta conflitos de alto risco (LOW/MEDIUM/HIGH)
  4. ScoringEngine        → Trinity Score v7 dinâmico e ponderado [0-100]
  5. ConfidenceEngine     → qualidade do sinal, alinhamento, signal_quality
  6. SignalGenerator      → sinal final LONG | SHORT | NEUTRAL com risk_level

Módulos:
  trinity_core.py         — Orquestrador principal + TrinityContext dataclass
  engine_orchestrator.py  — Execução e normalização dos 10 engines
  signal_consensus.py     — Consenso direcional por votação ponderada
  conflict_detector.py    — Detecção de conflito entre engines
  scoring_engine.py       — Trinity Score v7 com pesos dinâmicos
  confidence_engine.py    — Qualidade, alinhamento, credibilidade do sinal
  signal_generator.py     — Sinal final LONG/SHORT/NEUTRAL + risk_level
  meta_learning_engine.py — Engine 10: pesos adaptativos por histórico
"""

from trinity.core.trinity_core import TrinityCore, TrinityContext, run_trinity_core

__all__ = ["TrinityCore", "TrinityContext", "run_trinity_core"]
