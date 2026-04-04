"""
trinity — Pacote raiz do Trinity Core Intelligence Layer
Sistema de orquestração mestre do Trinity Trading System.

Uso:
    from trinity import run_trinity_core, TrinityContext

    ctx    = TrinityContext(symbol="BTCUSDT", df=df, price=price, ...)
    result = run_trinity_core(ctx)
"""
from trinity.core.trinity_core import TrinityCore, TrinityContext, run_trinity_core

__all__ = ["TrinityCore", "TrinityContext", "run_trinity_core"]
