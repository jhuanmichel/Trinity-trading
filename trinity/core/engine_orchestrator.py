"""
engine_orchestrator.py — Orquestrador dos 10 Engines do Trinity Core

Responsabilidades:
  - Executa todos os 10 engines em ordem lógica de dependência
  - Normaliza cada saída para o formato padrão EngineResult
  - Aplica fallback gracioso por engine (nunca deixa o pipeline travar)
  - Extrai score direcional [0-100] onde 50 = neutro, >50 = bullish, <50 = bearish

Ordem de execução:
  Grupo A (independentes): SMC, Market Maker, Liquidação, Geo, Cycle, Direction
  Grupo B (depende de A):  Pressure, Rare Setup
  Grupo C (depende de A+B): Neural, Meta

Formato de saída por engine (EngineResult):
  {
    "engine_id":          str,
    "direction":          BULLISH | BEARISH | NEUTRAL,
    "score":              0-100  (50 = neutro, >50 = bullish, <50 = bearish),
    "confidence":         0-100,
    "valid":              bool,
    "weight":             float  (peso no scoring — vem do meta engine),
    "data":               dict   (saída bruta do engine),
    "execution_time_ms":  float,
    "error":              str | None,
  }
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# ── Pesos padrão (substituídos pelo Meta Engine quando disponível) ────────────
DEFAULT_WEIGHTS: Dict[str, float] = {
    "smart_money":  0.15,
    "market_maker": 0.10,
    "liquidation":  0.05,
    "pressure":     0.10,
    "rare_setup":   0.08,
    "geopolitical": 0.07,
    "cycle":        0.10,
    "direction":    0.15,
    "neural":       0.15,
    "meta":         0.05,
}

# Mapeamento de direções brutas para canônico
_DIR_MAP = {
    "LONG":   "BULLISH", "BULL":     "BULLISH", "UP":       "BULLISH",
    "UPWARD": "BULLISH", "POSITIVE": "BULLISH", "BULLISH":  "BULLISH",
    "SHORT":  "BEARISH", "BEAR":     "BEARISH", "DOWN":     "BEARISH",
    "DOWNWARD":"BEARISH","NEGATIVE": "BEARISH", "BEARISH":  "BEARISH",
}
_CONF_STR_MAP = {
    "VERY_HIGH": 90.0, "HIGH": 78.0, "MEDIUM": 58.0,
    "LOW": 38.0, "VERY_LOW": 20.0,
}


# ── Dataclass de resultado ────────────────────────────────────────────────────

@dataclass
class EngineResult:
    """Resultado padronizado de qualquer um dos 10 engines."""
    engine_id:         str
    direction:         str              # BULLISH | BEARISH | NEUTRAL
    score:             float            # [0-100], 50 = neutro
    confidence:        float            # [0-100]
    valid:             bool
    weight:            float            # peso no Trinity Score
    data:              dict = field(default_factory=dict)
    execution_time_ms: float = 0.0
    error:             Optional[str] = None

    @property
    def is_bullish(self) -> bool:
        return self.direction == "BULLISH" and self.valid

    @property
    def is_bearish(self) -> bool:
        return self.direction == "BEARISH" and self.valid

    def to_dict(self) -> dict:
        return {
            "engine_id":         self.engine_id,
            "direction":         self.direction,
            "score":             round(self.score, 2),
            "confidence":        round(self.confidence, 2),
            "valid":             self.valid,
            "weight":            round(self.weight, 4),
            "execution_time_ms": round(self.execution_time_ms, 1),
            "error":             self.error,
        }


# ── Helpers de normalização ───────────────────────────────────────────────────

def _normalize_dir(raw: str) -> str:
    """Converte qualquer string direcional para BULLISH | BEARISH | NEUTRAL."""
    return _DIR_MAP.get(str(raw).upper().strip(), "NEUTRAL")


def _parse_confidence(raw) -> float:
    """Converte string ou número para float de confiança."""
    if isinstance(raw, (int, float)):
        return max(0.0, min(100.0, float(raw)))
    if isinstance(raw, str):
        return _CONF_STR_MAP.get(raw.upper(), 55.0)
    return 55.0


def _directional_score(direction: str, raw_score: float) -> float:
    """
    Converte (direção + score de força 0-100) para score direcional [0-100].

    Convenção: 50 = neutro. Se direction=BULLISH, score permanece como está
    (ex: 75 = bullish forte). Se BEARISH, espelha (ex: 75 forte bearish → 25).
    Se NEUTRAL, retorna exato 50.

    Engines onde raw_score JÁ é direcional (>50=bull, <50=bear) não devem
    usar esta função — usar o score direto.
    """
    if direction == "BULLISH":
        return max(0.0, min(100.0, float(raw_score)))
    elif direction == "BEARISH":
        return max(0.0, min(100.0, 100.0 - float(raw_score)))
    else:
        return 50.0


def _neutral_result(engine_id: str, weights: Dict[str, float],
                    elapsed: float, error: str = None) -> EngineResult:
    return EngineResult(
        engine_id=engine_id, direction="NEUTRAL", score=50.0,
        confidence=0.0, valid=False,
        weight=weights.get(engine_id, DEFAULT_WEIGHTS.get(engine_id, 0.05)),
        data={}, execution_time_ms=elapsed, error=error,
    )


# ── Extratores por engine ─────────────────────────────────────────────────────

def _extract_smc(data: dict) -> tuple:
    """Smart Money Engine → (direction, score, confidence)."""
    direction  = _normalize_dir(data.get("direction", "NEUTRAL"))
    raw_score  = float(data.get("smc_score", 50))
    confidence = _parse_confidence(data.get("confidence", "MEDIUM"))
    score      = _directional_score(direction, raw_score)
    return direction, score, confidence


def _extract_market_maker(data: dict) -> tuple:
    """Market Maker Engine → (direction, score, confidence)."""
    direction  = _normalize_dir(data.get("bias", "NEUTRAL"))
    raw_score  = float(data.get("market_maker_score", 50))
    confidence = _parse_confidence(data.get("confidence", "MEDIUM"))
    score      = _directional_score(direction, raw_score)
    return direction, score, confidence


def _extract_liquidation(data: dict) -> tuple:
    """
    Liquidation Engine (de liquidations.analyze) → (direction, score, confidence).

    Se o engine não retorna direção explícita, usa heurística de volume.
    """
    # Tenta direção explícita primeiro
    raw_dir = data.get("bias", data.get("direction", data.get("signal", "NEUTRAL")))
    direction = _normalize_dir(raw_dir)

    # Score: proporção de liquidações longas vs curtas como proxy de pressão
    long_vol  = float(data.get("long_liq_volume", data.get("long_volume", 0)))
    short_vol = float(data.get("short_liq_volume", data.get("short_volume", 0)))
    total_vol = long_vol + short_vol

    if total_vol > 0:
        # Muitas liquidações LONG → shorts vencendo → BEARISH
        bear_ratio = long_vol / total_vol
        # Score: 50 + diferença * 50 (bearish quando long_liq > short_liq)
        raw_score_liq = float(data.get("score", data.get("liq_score", 50)))
        if direction == "NEUTRAL":
            if bear_ratio > 0.65:
                direction  = "BEARISH"
            elif bear_ratio < 0.35:
                direction  = "BULLISH"
        score = _directional_score(direction, raw_score_liq if raw_score_liq != 50 else 60.0)
    else:
        raw_score_liq = float(data.get("score", data.get("liq_score", 50)))
        score = _directional_score(direction, raw_score_liq)

    confidence = _parse_confidence(data.get("confidence", 40.0))
    return direction, score, confidence


def _extract_pressure(data: dict) -> tuple:
    """
    Pressure Meter → (direction, score, confidence).

    pressure é signed [-100, +100]:
      > 0 → bullish pressure → score > 50
      < 0 → bearish pressure → score < 50
      = 0 → neutro           → score = 50
    """
    pressure   = float(data.get("pressure", 0.0))
    direction  = _normalize_dir(data.get("direction", "NEUTRAL"))
    # Remap: 50 + pressure * 0.5 → [0, 100]
    score      = max(0.0, min(100.0, 50.0 + pressure * 0.5))
    confidence = 72.0 if data.get("filter_passed") else 38.0
    return direction, score, confidence


def _extract_rare_setup(data: dict, inst_direction: str = "NEUTRAL") -> tuple:
    """
    Rare Setup Detector → (direction, score, confidence).

    Bonus de 0-100 amplifica a direção do sinal institucional.
    Sem setup raro → neutro.
    """
    is_rare  = bool(data.get("rare_setup", False))
    bonus    = float(data.get("signal_bonus", 0.0))
    raw_conf = float(data.get("score", 0.0))

    if not is_rare or bonus <= 0:
        return "NEUTRAL", 50.0, 0.0

    direction = _normalize_dir(inst_direction)
    # score: 50 ± bonus * 0.5 baseado na direção institucional
    if direction == "BULLISH":
        score = min(100.0, 50.0 + bonus * 0.5)
    elif direction == "BEARISH":
        score = max(0.0, 50.0 - bonus * 0.5)
    else:
        score = 50.0  # sem direção clara, setup raro não direciona

    confidence = min(90.0, raw_conf)
    return direction, score, confidence


def _extract_geopolitical(data: dict) -> tuple:
    """Geopolitical Engine → (direction, score, confidence)."""
    direction  = _normalize_dir(data.get("geo_bias", "NEUTRAL"))
    raw_score  = float(data.get("geo_score", 50))
    confidence = float(data.get("geo_confidence", 0.0))
    score      = _directional_score(direction, raw_score)
    return direction, score, confidence


def _extract_cycle(data: dict) -> tuple:
    """Bitcoin Cycle Engine → (direction, score, confidence)."""
    direction  = _normalize_dir(data.get("bias_adjustment", "NEUTRAL"))
    raw_score  = float(data.get("cycle_score", 50))
    confidence = float(data.get("cycle_confidence", 0.0))
    score      = _directional_score(direction, raw_score)
    return direction, score, confidence


def _extract_direction(data: dict) -> tuple:
    """
    Institutional Direction Engine → (direction, score, confidence).

    direction_score É direcional: >50 = bullish, <50 = bearish.
    Não aplica _directional_score() — usa diretamente.
    """
    direction  = _normalize_dir(data.get("direction", "NEUTRAL"))
    score      = float(data.get("direction_score", 50.0))   # já direcional
    confidence = float(data.get("direction_confidence", 50.0))
    return direction, score, confidence


def _extract_neural(data: dict) -> tuple:
    """
    Neural Intelligence Engine → (direction, score, confidence).

    neural_score É direcional: >50 = bullish, <50 = bearish.
    """
    direction  = _normalize_dir(data.get("neural_bias", "NEUTRAL"))
    score      = float(data.get("neural_score", 50.0))      # já direcional
    confidence = float(data.get("confidence_score", 20.0))
    return direction, score, confidence


def _extract_meta(data: dict) -> tuple:
    """Meta Learning Engine → (direction, score, confidence)."""
    direction  = _normalize_dir(data.get("direction", "NEUTRAL"))
    score      = float(data.get("meta_score", 50.0))        # já direcional
    confidence = float(data.get("confidence", 0.0))
    return direction, score, confidence


# ── Orquestrador principal ────────────────────────────────────────────────────

class EngineOrchestrator:
    """
    Executa todos os 10 engines e retorna lista de EngineResult padronizados.

    Aceita dados pré-computados via TrinityContext para evitar chamadas duplicadas
    de API. Se um dado não está disponível, executa o engine diretamente.
    """

    def run(self, ctx: "TrinityContext") -> Dict[str, EngineResult]:
        """
        Ponto de entrada principal.

        Returns:
            dict[engine_id, EngineResult] — um entry por engine
        """
        # Obtém pesos adaptativos do Meta Engine (roda primeiro para calibrar pesos)
        weights = self._get_weights(ctx)

        results: Dict[str, EngineResult] = {}

        # ── Grupo A: engines independentes ───────────────────────────────────
        results["smart_money"]  = self._run_smc(ctx, weights)
        results["market_maker"] = self._run_market_maker(ctx, weights)
        results["liquidation"]  = self._run_liquidation(ctx, weights)
        results["geopolitical"] = self._run_geopolitical(ctx, weights)
        results["cycle"]        = self._run_cycle(ctx, weights)
        results["direction"]    = self._run_direction(ctx, weights)

        # ── Grupo B: engines que dependem de A ───────────────────────────────
        inst_dir = (ctx.inst_data or {}).get("direction", "NEUTRAL")
        results["pressure"]   = self._run_pressure(ctx, weights)
        results["rare_setup"] = self._run_rare_setup(ctx, weights, inst_dir)

        # ── Grupo C: engines que dependem de A + B ───────────────────────────
        results["neural"] = self._run_neural(ctx, weights)
        results["meta"]   = self._run_meta(ctx, weights)

        total_ms = sum(r.execution_time_ms for r in results.values())
        valid_n  = sum(1 for r in results.values() if r.valid)
        log.info(
            f"   [Orchestrator] {valid_n}/10 engines válidos | "
            f"tempo total: {total_ms:.0f}ms"
        )
        return results

    # ── Getter de pesos ───────────────────────────────────────────────────────

    def _get_weights(self, ctx: "TrinityContext") -> Dict[str, float]:
        """Retorna pesos do Meta Engine se disponível, senão pesos padrão."""
        if ctx.meta_data and ctx.meta_data.get("engine_weights"):
            return ctx.meta_data["engine_weights"]
        return DEFAULT_WEIGHTS.copy()

    # ── Runners individuais ───────────────────────────────────────────────────

    def _run_smc(self, ctx, weights) -> EngineResult:
        t0 = time.monotonic()
        try:
            data = ctx.smc_data
            if data is None:
                from smart_money_engine import SmartMoneyEngine
                engine = SmartMoneyEngine(ctx.df, ctx.symbol)
                data   = engine.analyze()
            direction, score, conf = _extract_smc(data)
            return EngineResult(
                engine_id="smart_money", direction=direction, score=score,
                confidence=conf, valid=True,
                weight=weights.get("smart_money", DEFAULT_WEIGHTS["smart_money"]),
                data=data, execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return _neutral_result("smart_money", weights, (time.monotonic() - t0)*1000, str(e))

    def _run_market_maker(self, ctx, weights) -> EngineResult:
        t0 = time.monotonic()
        try:
            data = ctx.mm_data
            if data is None:
                from market_maker_engine import run_market_maker_analysis
                data = run_market_maker_analysis(
                    symbol            = ctx.symbol,
                    df                = ctx.df,
                    trend_score       = (ctx.trend_data or {}).get("score", 50),
                    correlation_score = (ctx.corr_data or {}).get("score", 50),
                )
            direction, score, conf = _extract_market_maker(data)
            return EngineResult(
                engine_id="market_maker", direction=direction, score=score,
                confidence=conf, valid=True,
                weight=weights.get("market_maker", DEFAULT_WEIGHTS["market_maker"]),
                data=data, execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return _neutral_result("market_maker", weights, (time.monotonic() - t0)*1000, str(e))

    def _run_liquidation(self, ctx, weights) -> EngineResult:
        t0 = time.monotonic()
        try:
            data = ctx.liq_data
            if data is None:
                from indicators import liquidations
                data = liquidations.analyze("BTC")
            direction, score, conf = _extract_liquidation(data)
            return EngineResult(
                engine_id="liquidation", direction=direction, score=score,
                confidence=conf, valid=True,
                weight=weights.get("liquidation", DEFAULT_WEIGHTS["liquidation"]),
                data=data, execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return _neutral_result("liquidation", weights, (time.monotonic() - t0)*1000, str(e))

    def _run_geopolitical(self, ctx, weights) -> EngineResult:
        t0 = time.monotonic()
        try:
            data = ctx.geo_data
            if data is None:
                from geopolitical_engine import run_geo_analysis
                data = run_geo_analysis()
            direction, score, conf = _extract_geopolitical(data)
            return EngineResult(
                engine_id="geopolitical", direction=direction, score=score,
                confidence=conf, valid=True,
                weight=weights.get("geopolitical", DEFAULT_WEIGHTS["geopolitical"]),
                data=data, execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return _neutral_result("geopolitical", weights, (time.monotonic() - t0)*1000, str(e))

    def _run_cycle(self, ctx, weights) -> EngineResult:
        t0 = time.monotonic()
        try:
            data = ctx.cycle_data
            if data is None:
                from bitcoin_cycle_engine import run_bitcoin_cycle_analysis
                data = run_bitcoin_cycle_analysis()
            direction, score, conf = _extract_cycle(data)
            return EngineResult(
                engine_id="cycle", direction=direction, score=score,
                confidence=conf, valid=True,
                weight=weights.get("cycle", DEFAULT_WEIGHTS["cycle"]),
                data=data, execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return _neutral_result("cycle", weights, (time.monotonic() - t0)*1000, str(e))

    def _run_direction(self, ctx, weights) -> EngineResult:
        t0 = time.monotonic()
        try:
            data = ctx.direction_data
            if data is None:
                from institutional_direction_engine import run_institutional_direction_analysis
                data = run_institutional_direction_analysis(ctx.symbol)
            direction, score, conf = _extract_direction(data)
            return EngineResult(
                engine_id="direction", direction=direction, score=score,
                confidence=conf, valid=True,
                weight=weights.get("direction", DEFAULT_WEIGHTS["direction"]),
                data=data, execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return _neutral_result("direction", weights, (time.monotonic() - t0)*1000, str(e))

    def _run_pressure(self, ctx, weights) -> EngineResult:
        t0 = time.monotonic()
        try:
            data = ctx.pressure_data
            if data is None:
                from pressure_meter import calculate_pressure
                from btc_liquidation_engine import get_for_scoring as _liq_scoring
                data = calculate_pressure(
                    liq_scoring    = _liq_scoring(),
                    volume_data    = ctx.volume_data or {},
                    inst_breakdown = (ctx.inst_data or {}).get("breakdown", {}),
                    mm_data        = ctx.mm_data,
                    trend_data     = ctx.trend_data or {},
                    deriv_data     = ctx.deriv_data or {},
                    price          = ctx.price,
                )
            direction, score, conf = _extract_pressure(data)
            return EngineResult(
                engine_id="pressure", direction=direction, score=score,
                confidence=conf, valid=True,
                weight=weights.get("pressure", DEFAULT_WEIGHTS["pressure"]),
                data=data, execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return _neutral_result("pressure", weights, (time.monotonic() - t0)*1000, str(e))

    def _run_rare_setup(self, ctx, weights, inst_direction: str = "NEUTRAL") -> EngineResult:
        t0 = time.monotonic()
        try:
            data = ctx.rare_data
            if data is None:
                from rare_setup_detector import detect_rare_setup
                from btc_liquidation_engine import get_for_scoring as _liq_scoring
                data = detect_rare_setup(
                    smc_signal  = ctx.smc_data,
                    mm_data     = ctx.mm_data,
                    liq_scoring = _liq_scoring(),
                    volume_data = ctx.volume_data or {},
                    trend_data  = ctx.trend_data or {},
                    price       = ctx.price,
                    direction   = inst_direction,
                )
            direction, score, conf = _extract_rare_setup(data, inst_direction)
            return EngineResult(
                engine_id="rare_setup", direction=direction, score=score,
                confidence=conf, valid=True,
                weight=weights.get("rare_setup", DEFAULT_WEIGHTS["rare_setup"]),
                data=data, execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return _neutral_result("rare_setup", weights, (time.monotonic() - t0)*1000, str(e))

    def _run_neural(self, ctx, weights) -> EngineResult:
        t0 = time.monotonic()
        try:
            data = ctx.neural_data
            if data is None:
                from neural_engine import run_neural_analysis
                smc_mtf = (ctx.smc_data or {}).get("mtf_analysis")
                data = run_neural_analysis(
                    symbol         = ctx.symbol,
                    df             = ctx.df,
                    price          = ctx.price,
                    inst_data      = ctx.inst_data or {},
                    smc_data       = ctx.smc_data,
                    mm_data        = ctx.mm_data,
                    pressure_data  = ctx.pressure_data,
                    rare_data      = ctx.rare_data,
                    geo_data       = ctx.geo_data,
                    cycle_data     = ctx.cycle_data,
                    direction_data = ctx.direction_data,
                    regime_data    = ctx.regime_data or {},
                    volume_data    = ctx.volume_data or {},
                    trend_data     = ctx.trend_data or {},
                    deriv_data     = ctx.deriv_data or {},
                    smc_mtf_data   = smc_mtf,
                )
            direction, score, conf = _extract_neural(data)
            return EngineResult(
                engine_id="neural", direction=direction, score=score,
                confidence=conf, valid=True,
                weight=weights.get("neural", DEFAULT_WEIGHTS["neural"]),
                data=data, execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return _neutral_result("neural", weights, (time.monotonic() - t0)*1000, str(e))

    def _run_meta(self, ctx, weights) -> EngineResult:
        t0 = time.monotonic()
        try:
            data = ctx.meta_data
            if data is None:
                from trinity.core.meta_learning_engine import run_meta_analysis
                data = run_meta_analysis()
            direction, score, conf = _extract_meta(data)
            return EngineResult(
                engine_id="meta", direction=direction, score=score,
                confidence=conf, valid=True,
                weight=weights.get("meta", DEFAULT_WEIGHTS["meta"]),
                data=data, execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return _neutral_result("meta", weights, (time.monotonic() - t0)*1000, str(e))
