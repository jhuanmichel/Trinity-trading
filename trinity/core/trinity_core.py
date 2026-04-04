"""
trinity_core.py — Orquestrador Principal do Trinity Core

Ponto de entrada único para todo o pipeline de inteligência institucional.
Orquestra os 6 estágios internos e entrega o sinal de trade final.

Pipeline:
  1. EngineOrchestrator  → executa os 10 engines, normaliza para EngineResult
  2. SignalConsensus      → consenso direcional ponderado (BULLISH/BEARISH/NEUTRAL)
  3. ConflictDetector     → nível de conflito entre engines (LOW/MEDIUM/HIGH)
  4. ScoringEngine        → Trinity Score v7 [0-100] com regime e penalidade
  5. ConfidenceEngine     → qualidade do sinal, alinhamento, credibilidade
  6. SignalGenerator      → sinal final LONG | SHORT | NEUTRAL + risk_level

TrinityContext:
  Dataclass que carrega todos os dados pré-computados pelos engines individuais.
  Engines que já rodaram antes (ex: neural em main.py) passam seus dados aqui.
  Se um campo for None, o EngineOrchestrator executa o engine correspondente.

Saída final:
  {
    "signal":               LONG | SHORT | NEUTRAL,
    "signal_strength":      STRONG | MODERATE | WEAK | NEUTRAL,
    "confidence":           0-100,
    "trinity_score":        0-100,
    "risk_level":           LOW | MEDIUM | HIGH,
    "market_bias":          BULLISH | BEARISH | NEUTRAL,
    "trade_allowed":        bool,
    "position_size_factor": 0-1.0,
    "signal_rationale":     str,
    "alert_priority":       CRITICAL | HIGH | MEDIUM | LOW,
    "consensus":            BULLISH | BEARISH | NEUTRAL,
    "conflict":             bool,
    "conflict_level":       LOW | MEDIUM | HIGH,
    "engine_alignment":     0-100,   # dominant_pct do consenso
    "market_regime":        str,
    "scoring":              dict,    # saída completa do ScoringEngine
    "consensus_data":       dict,
    "conflict_data":        dict,
    "confidence_data":      dict,
    "engine_results":       dict,    # EngineResult.to_dict() por engine
    "timestamp":            str,
    "symbol":               str,
    "pipeline_ms":          float,   # tempo total do pipeline em ms
  }
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# ── Cache em memória (TTL = 5 min por símbolo) ────────────────────────────────
CACHE_TTL = 300   # segundos
_cache: Dict[str, dict] = {}    # {symbol: {"result": ..., "ts": float}}


# ── TrinityContext ─────────────────────────────────────────────────────────────

@dataclass
class TrinityContext:
    """
    Contexto de execução do Trinity Core.

    Carrega dados pré-computados de cada engine.
    Campos None são calculados on-demand pelo EngineOrchestrator.
    """
    # ── Identificação ──────────────────────────────────────────────────────────
    symbol:  str = "BTCUSDT"
    price:   float = 0.0
    df:      Any = None          # pd.DataFrame com OHLCV

    # ── Dados dos engines (None = executa on-demand) ──────────────────────────
    smc_data:       Optional[dict] = None   # Smart Money Engine
    mm_data:        Optional[dict] = None   # Market Maker Engine
    liq_data:       Optional[dict] = None   # Liquidation Engine
    pressure_data:  Optional[dict] = None   # Pressure Meter
    rare_data:      Optional[dict] = None   # Rare Setup Detector
    geo_data:       Optional[dict] = None   # Geopolitical Engine
    cycle_data:     Optional[dict] = None   # Bitcoin Cycle Engine
    direction_data: Optional[dict] = None   # Institutional Direction Engine
    neural_data:    Optional[dict] = None   # Neural Intelligence Engine
    meta_data:      Optional[dict] = None   # Meta Learning Engine

    # ── Dados auxiliares usados pelos engines ─────────────────────────────────
    inst_data:      Optional[dict] = None   # saída do scoring institucional legado
    volume_data:    Optional[dict] = None   # análise de volume
    trend_data:     Optional[dict] = None   # análise de tendência
    corr_data:      Optional[dict] = None   # correlação BTC/alts
    deriv_data:     Optional[dict] = None   # dados de derivativos (funding, OI)
    regime_data:    Optional[dict] = None   # regime de mercado (ADX, ATR, etc.)

    def market_regime(self) -> str:
        """
        Extrai o regime de mercado do contexto.
        Prioridade: neural_data → regime_data → 'RANGING' (padrão seguro).
        """
        if self.neural_data:
            r = self.neural_data.get("market_regime")
            if r:
                return str(r).upper()
        if self.regime_data:
            r = self.regime_data.get("regime") or self.regime_data.get("market_regime")
            if r:
                return str(r).upper()
        return "RANGING"


# ── TrinityCore ────────────────────────────────────────────────────────────────

class TrinityCore:
    """
    Orquestrador principal do Trinity Core.

    Instância stateless — pode ser reutilizada entre ciclos.
    Todo estado de execução vive no TrinityContext passado.
    """

    def __init__(self) -> None:
        # Import tardio para evitar circular imports
        from trinity.core.engine_orchestrator import EngineOrchestrator
        self._orchestrator = EngineOrchestrator()

    def analyze(self, ctx: TrinityContext) -> dict:
        """
        Executa o pipeline completo e retorna o sinal final.

        Args:
            ctx: TrinityContext com dados pré-computados (ou None para on-demand)

        Returns:
            dict com sinal final + todos os metadados do pipeline
        """
        t_start = time.monotonic()

        # ── Imports internos ──────────────────────────────────────────────────
        from trinity.core.signal_consensus import calculate_consensus
        from trinity.core.conflict_detector import detect_conflict
        from trinity.core.scoring_engine import calculate_score
        from trinity.core.confidence_engine import calculate_confidence
        from trinity.core.signal_generator import generate_signal

        log.info(f"[TrinityCore] Iniciando pipeline | symbol={ctx.symbol} | price={ctx.price:.2f}")

        # ── Estágio 1: Execução dos 10 engines ───────────────────────────────
        t1 = time.monotonic()
        engine_results = self._orchestrator.run(ctx)
        ms1 = (time.monotonic() - t1) * 1000
        log.debug(f"   [Stage 1] EngineOrchestrator: {ms1:.0f}ms")

        # ── Estágio 2: Consenso direcional ────────────────────────────────────
        t2 = time.monotonic()
        consensus_data = calculate_consensus(engine_results)
        ms2 = (time.monotonic() - t2) * 1000
        log.debug(f"   [Stage 2] Consensus: {consensus_data['consensus']} | {ms2:.0f}ms")

        # ── Estágio 3: Detecção de conflitos ──────────────────────────────────
        t3 = time.monotonic()
        conflict_data = detect_conflict(engine_results, consensus_data)
        ms3 = (time.monotonic() - t3) * 1000
        log.debug(f"   [Stage 3] Conflict: {conflict_data['conflict_level']} | {ms3:.0f}ms")

        # ── Estágio 4: Trinity Score v7 ───────────────────────────────────────
        t4 = time.monotonic()
        market_regime  = ctx.market_regime()
        conflict_score = conflict_data.get("conflict_score", 0.0)
        scoring_data   = calculate_score(engine_results, market_regime, conflict_score)
        ms4 = (time.monotonic() - t4) * 1000
        log.debug(
            f"   [Stage 4] Score: {scoring_data['trinity_score']:.1f} | "
            f"regime={market_regime} | {ms4:.0f}ms"
        )

        # ── Estágio 5: Confiança e qualidade ──────────────────────────────────
        t5 = time.monotonic()
        confidence_data = calculate_confidence(
            engine_results, consensus_data, conflict_data, scoring_data
        )
        ms5 = (time.monotonic() - t5) * 1000
        log.debug(
            f"   [Stage 5] Confidence: {confidence_data['confidence']:.0f}% | "
            f"quality={confidence_data['signal_quality']} | {ms5:.0f}ms"
        )

        # ── Estágio 6: Sinal final ─────────────────────────────────────────────
        t6 = time.monotonic()
        signal_data = generate_signal(
            scoring_data, consensus_data, conflict_data, confidence_data
        )
        ms6 = (time.monotonic() - t6) * 1000
        log.debug(
            f"   [Stage 6] Signal: {signal_data['signal']} ({signal_data['signal_strength']}) | "
            f"risk={signal_data['risk_level']} | {ms6:.0f}ms"
        )

        # ── Monta resultado final ─────────────────────────────────────────────
        pipeline_ms = (time.monotonic() - t_start) * 1000

        result = {
            # Sinal principal
            "signal":               signal_data["signal"],
            "signal_strength":      signal_data["signal_strength"],
            "confidence":           signal_data["confidence"],
            "trinity_score":        signal_data["trinity_score"],
            "risk_level":           signal_data["risk_level"],
            "market_bias":          signal_data["market_bias"],
            "trade_allowed":        signal_data["trade_allowed"],
            "position_size_factor": signal_data["position_size_factor"],
            "signal_rationale":     signal_data["signal_rationale"],
            "alert_priority":       signal_data["alert_priority"],

            # Consenso + conflito
            "consensus":        consensus_data["consensus"],
            "conflict":         conflict_data["conflict"],
            "conflict_level":   conflict_data["conflict_level"],
            "engine_alignment": round(consensus_data["dominant_pct"], 1),

            # Regime e metadados de contexto
            "market_regime":    market_regime,
            "symbol":           ctx.symbol,
            "timestamp":        datetime.now(timezone.utc).isoformat(),
            "pipeline_ms":      round(pipeline_ms, 1),

            # Sub-dicts completos (para logging / dashboard)
            "scoring":          scoring_data,
            "consensus_data":   consensus_data,
            "conflict_data":    conflict_data,
            "confidence_data":  confidence_data,
            "engine_results":   {k: v.to_dict() for k, v in engine_results.items()},
        }

        log.info(
            f"[TrinityCore] Concluído | {signal_data['signal']} ({signal_data['signal_strength']}) | "
            f"score={signal_data['trinity_score']:.1f} | conf={signal_data['confidence']:.0f}% | "
            f"risk={signal_data['risk_level']} | pipeline={pipeline_ms:.0f}ms"
        )

        return result


# ── Singleton e cache ──────────────────────────────────────────────────────────

_instance: Optional[TrinityCore] = None


def _get_instance() -> TrinityCore:
    global _instance
    if _instance is None:
        _instance = TrinityCore()
    return _instance


def run_trinity_core(ctx: TrinityContext, use_cache: bool = True) -> dict:
    """
    Ponto de entrada público do Trinity Core.

    Executa o pipeline completo com cache de 5 minutos por símbolo.

    Args:
        ctx:       TrinityContext com dados pré-computados
        use_cache: se True (padrão), retorna resultado cacheado se < CACHE_TTL segundos

    Returns:
        dict com sinal final e todos os metadados
    """
    if use_cache:
        cached = _cache.get(ctx.symbol)
        if cached and (time.time() - cached["ts"]) < CACHE_TTL:
            log.debug(f"[TrinityCore] Cache hit para {ctx.symbol}")
            return cached["result"]

    core   = _get_instance()
    result = core.analyze(ctx)

    _cache[ctx.symbol] = {"result": result, "ts": time.time()}
    return result
