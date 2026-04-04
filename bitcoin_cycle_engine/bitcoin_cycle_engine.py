"""
bitcoin_cycle_engine.py — Bitcoin Cycle Engine (Orquestrador Principal)
Trinity Trading | Bitcoin Cycle Engine v1.0

Pipeline:
  1. Trend Model        → 200W MA, drawdown, tendência macro
  2. Halving Model      → posição no ciclo de halving
  3. On-chain Model     → MVRV, NUPL, comportamento smart money
  4. Volatility Model   → fase de volatilidade, ATR, volume
  5. Macro Detector     → agrega modelos, score macro
  6. Cycle Classifier   → ACCUMULATION → CAPITULATION
  7. Cycle Scoring      → score 0-100
  8. Rare Cycle Setup   → combinações raras de alto impacto
  9. Final Output

Output:
  cycle_phase, cycle_confidence, cycle_strength, cycle_score,
  risk_level, risk_multiplier, bias_adjustment,
  macro_trend, cycle_position_pct, expected_volatility,
  rare_cycle_setup, phase_description, active_signals,
  halving, trend, onchain, volatility (sub-dicts)
"""

import logging
import time
from typing import Optional

from .trend_model          import run_trend_model
from .halving_model        import run_halving_model
from .onchain_model        import run_onchain_model
from .volatility_model     import run_volatility_model
from .macro_cycle_detector import detect_macro_phase
from .cycle_classifier     import classify_cycle_phase, CyclePhase, PHASE_DESCRIPTIONS
from .cycle_scoring        import calculate_cycle_score, get_risk_multiplier, get_bias_adjustment

log = logging.getLogger(__name__)

# ─── Cache (1 hora — dados macro mudam lentamente) ───────────────────────────

_cycle_cache:    Optional[dict] = None
_cycle_cache_ts: float          = 0.0
CYCLE_CACHE_TTL = 3600           # 60 minutos


# ─── Rare Cycle Setup ─────────────────────────────────────────────────────────

# Combinações raras que sinalizam setups de alta convicção
RARE_CYCLE_COMBOS = [
    # EARLY_BULL + short liquidations no derivativos (combustível)
    {"phase": "EARLY_BULL",   "signal": "early_bull_short_fuel"},
    # MID_BULL + acumulação institucional ativa
    {"phase": "MID_BULL",     "signal": "mid_bull_inst_accum"},
    # ACCUMULATION + múltiplos sinais de fundo (MVRV baixo + halving early)
    {"phase": "ACCUMULATION", "signal": "accumulation_halving_early"},
    # CAPITULATION + long liquidation cascade (fundo histórico)
    {"phase": "CAPITULATION", "signal": "capitulation_long_cascade"},
]


def _detect_rare_cycle_setup(
    phase:       CyclePhase,
    halving_data: dict,
    onchain_data: dict,
    volatility_data: dict,
    macro_data:  dict,
) -> dict:
    """
    Detecta combinações raras de alto impacto no ciclo.

    Returns:
        {
            "active":        bool,
            "setup_type":    str | None,
            "description":   str | None,
        }
    """
    signals = macro_data.get("signals", [])
    hal_phase = halving_data.get("halving_phase", "MID")
    mvrv      = onchain_data.get("mvrv", None)
    sm_beh    = onchain_data.get("smart_money_behavior", "NEUTRAL")
    vol_trend = volatility_data.get("vol_trend", "STABLE")
    atr_exp   = volatility_data.get("atr_expansion", False)

    setup_type  = None
    description = None

    # EARLY_BULL + smart money acumulando agressivamente
    if (phase == CyclePhase.EARLY_BULL
            and sm_beh == "ACCUMULATING"
            and hal_phase in ("EARLY", "ACCUMULATION")
            and "smart_money_acumulando" in signals):
        setup_type  = "EARLY_BULL_ACCUMULATION"
        description = "Early Bull + Smart Money acumulando + Halving recente"

    # MID_BULL + tendência forte + MVRV saudável
    elif (phase == CyclePhase.MID_BULL
            and "tendencia_UP" in signals
            and "acima_200W_MA" in signals
            and "acima_50W_MA" in signals
            and sm_beh == "ACCUMULATING"):
        setup_type  = "MID_BULL_INSTITUTIONAL"
        description = "Mid Bull + Acumulacao institucional + Trend forte acima 200W/50W"

    # ACCUMULATION + halving early + MVRV baixo (fundo de ciclo)
    elif (phase == CyclePhase.ACCUMULATION
            and hal_phase in ("ACCUMULATION", "EARLY")
            and mvrv is not None and mvrv < 1.2):
        setup_type  = "CYCLE_BOTTOM_ACCUMULATION"
        description = f"Fundo de ciclo: MVRV={mvrv:.2f}, halving {hal_phase}, acumulacao ativa"

    # CAPITULATION + vol expandindo + atr expansion (cascata de liquidações)
    elif (phase == CyclePhase.CAPITULATION
            and atr_exp
            and vol_trend == "EXPANDING"):
        setup_type  = "CAPITULATION_CASCADE"
        description = "Capitulacao + cascata de liquidacoes + ATR expandindo"

    active = setup_type is not None

    return {
        "active":        active,
        "setup_type":    setup_type,
        "description":   description,
    }


# ─── Output ───────────────────────────────────────────────────────────────────

def _build_output(
    phase:           CyclePhase,
    cycle_score:     float,
    trend_data:      dict,
    halving_data:    dict,
    onchain_data:    dict,
    volatility_data: dict,
    macro_data:      dict,
    rare_setup:      dict,
) -> dict:
    """Monta o output padronizado do Bitcoin Cycle Engine."""

    risk_mult  = get_risk_multiplier(phase)
    bias_adj   = get_bias_adjustment(phase, macro_data.get("macro_score", 50.0))
    cycle_age  = halving_data.get("cycle_age_pct",   50.0)
    vol_phase  = volatility_data.get("volatility_phase", "MEDIUM")

    # Confiança: baseada na quantidade de sinais convergentes
    n_signals   = len(macro_data.get("signals", []))
    confidence  = round(min(95.0, 40.0 + n_signals * 7.0), 1)

    # Força do ciclo
    if   cycle_score >= 75: cycle_strength = "STRONG"
    elif cycle_score >= 55: cycle_strength = "MODERATE"
    elif cycle_score >= 40: cycle_strength = "WEAK"
    else:                   cycle_strength = "VERY_WEAK"

    # Risk level por fase
    _risk_map = {
        CyclePhase.ACCUMULATION: "MEDIUM",
        CyclePhase.EARLY_BULL:   "MEDIUM",
        CyclePhase.MID_BULL:     "MEDIUM",
        CyclePhase.LATE_BULL:    "HIGH",
        CyclePhase.DISTRIBUTION: "HIGH",
        CyclePhase.BEAR:         "HIGH",
        CyclePhase.CAPITULATION: "VERY_HIGH",
    }
    risk_level = _risk_map.get(phase, "MEDIUM")

    # Macro trend
    macro_bias  = macro_data.get("bias", "NEUTRAL")
    macro_trend = macro_bias   # já é BULLISH / BEARISH / NEUTRAL

    return {
        # ── Output principal ──────────────────────────────────────────────
        "cycle_phase":         phase.value,
        "cycle_confidence":    confidence,
        "cycle_strength":      cycle_strength,
        "cycle_score":         cycle_score,

        # ── Risco e bias ──────────────────────────────────────────────────
        "risk_level":          risk_level,
        "risk_multiplier":     risk_mult,
        "bias_adjustment":     bias_adj,

        # ── Macro ─────────────────────────────────────────────────────────
        "macro_trend":         macro_trend,
        "macro_score":         macro_data.get("macro_score", 50.0),
        "cycle_position_pct":  cycle_age,
        "expected_volatility": vol_phase,

        # ── Rare Cycle Setup ──────────────────────────────────────────────
        "rare_cycle_setup":    rare_setup.get("active",      False),
        "rare_cycle_type":     rare_setup.get("setup_type",  None),
        "rare_cycle_desc":     rare_setup.get("description", None),

        # ── Descrição e sinais ────────────────────────────────────────────
        "phase_description":   PHASE_DESCRIPTIONS.get(phase, ""),
        "active_signals":      macro_data.get("signals", []),

        # ── Sub-modelos ───────────────────────────────────────────────────
        "halving": {
            "phase":         halving_data.get("halving_phase"),
            "score":         halving_data.get("halving_score"),
            "days_since":    halving_data.get("days_since_halving"),
            "days_to_next":  halving_data.get("days_to_next"),
            "cycle_age_pct": halving_data.get("cycle_age_pct"),
        },
        "trend": {
            "direction":       trend_data.get("trend_direction"),
            "strength":        trend_data.get("trend_strength"),
            "above_200w_ma":   trend_data.get("above_200w_ma"),
            "above_50w_ma":    trend_data.get("above_50w_ma"),
            "ma_200w":         trend_data.get("ma_200w"),
            "ma_50w":          trend_data.get("ma_50w"),
            "drawdown_pct":    trend_data.get("drawdown_pct"),
            "macro_structure": trend_data.get("macro_structure"),
        },
        "onchain": {
            "strength":        onchain_data.get("onchain_strength"),
            "behavior":        onchain_data.get("smart_money_behavior"),
            "mvrv":            onchain_data.get("mvrv"),
            "nupl":            onchain_data.get("nupl"),
            "nupl_phase":      onchain_data.get("nupl_phase"),
            "realized_price":  onchain_data.get("realized_price"),
            "data_source":     onchain_data.get("data_source"),
        },
        "volatility": {
            "phase":           volatility_data.get("volatility_phase"),
            "score":           volatility_data.get("volatility_score"),
            "atr_pct_weekly":  volatility_data.get("atr_pct_weekly"),
            "vol_trend":       volatility_data.get("vol_trend"),
            "atr_expansion":   volatility_data.get("atr_expansion"),
        },
    }


# ─── Engine ───────────────────────────────────────────────────────────────────

class BitcoinCycleEngine:
    """
    Motor de análise de ciclo Bitcoin (arquitetura hedge fund).

    Uso:
        engine = BitcoinCycleEngine()
        result = engine.run()
    """

    def run(self, force: bool = False, external_onchain: Optional[dict] = None) -> dict:
        """
        Executa o pipeline completo de análise de ciclo.

        Args:
            force:            ignora cache e força re-execução
            external_onchain: dict do indicators/onchain.py — melhora onchain model

        Returns:
            dict completo com cycle_phase, cycle_score, bias_adjustment, etc.
        """
        global _cycle_cache, _cycle_cache_ts

        if not force and _cycle_cache is not None:
            if (time.time() - _cycle_cache_ts) < CYCLE_CACHE_TTL:
                log.debug("   Cycle engine: cache hit")
                return _cycle_cache

        log.info("   Cycle engine: iniciando pipeline...")
        t0 = time.time()

        try:
            # ── 1. Trend Model ─────────────────────────────────────────────
            log.info("   Cycle [1/7] Trend Model (200W MA, drawdown)...")
            trend_data = run_trend_model()
            log.info(
                f"   Trend: dir={trend_data['trend_direction']} | "
                f"str={trend_data['trend_strength']:.0f} | "
                f"200W={'✅' if trend_data['above_200w_ma'] else '❌' if trend_data['above_200w_ma'] is not None else '?'} | "
                f"DD={trend_data['drawdown_pct']:.1f}%"
            )

            # ── 2. Halving Model ───────────────────────────────────────────
            log.info("   Cycle [2/7] Halving Model...")
            halving_data = run_halving_model()
            log.info(
                f"   Halving: {halving_data['halving_phase']} | "
                f"score={halving_data['halving_score']:.0f} | "
                f"{halving_data['days_since_halving']}d desde último | "
                f"ciclo {halving_data['cycle_age_pct']:.0f}%"
            )

            # ── 3. On-chain Model ──────────────────────────────────────────
            log.info("   Cycle [3/7] On-chain Model (MVRV, NUPL)...")
            onchain_data = run_onchain_model(external_onchain=external_onchain)
            log.info(
                f"   Onchain: str={onchain_data['onchain_strength']:.0f} | "
                f"behavior={onchain_data['smart_money_behavior']} | "
                f"MVRV={onchain_data['mvrv']} | "
                f"NUPL={onchain_data['nupl']} ({onchain_data['nupl_phase']}) | "
                f"src={onchain_data['data_source']}"
            )

            # ── 4. Volatility Model ────────────────────────────────────────
            log.info("   Cycle [4/7] Volatility Model...")
            volatility_data = run_volatility_model()
            log.info(
                f"   Volatility: {volatility_data['volatility_phase']} | "
                f"score={volatility_data['volatility_score']:.0f} | "
                f"ATR%={volatility_data['atr_pct_weekly']:.1f}% | "
                f"trend={volatility_data['vol_trend']}"
            )

            # ── 5. Macro Detector ──────────────────────────────────────────
            log.info("   Cycle [5/7] Macro Detector...")
            macro_data = detect_macro_phase(trend_data, halving_data, onchain_data, volatility_data)
            log.info(
                f"   Macro: {macro_data['macro_phase']} | "
                f"score={macro_data['macro_score']:.0f} | "
                f"bias={macro_data['bias']} | "
                f"signals={len(macro_data['signals'])}"
            )

            # ── 6. Cycle Classifier ────────────────────────────────────────
            log.info("   Cycle [6/7] Classifier...")
            phase = classify_cycle_phase(
                trend_data, halving_data, onchain_data, volatility_data, macro_data
            )
            log.info(f"   Phase: {phase.value}")

            # ── 7. Cycle Scoring ───────────────────────────────────────────
            log.info("   Cycle [7/7] Scoring...")
            cycle_score = calculate_cycle_score(
                trend_data, halving_data, onchain_data, volatility_data
            )
            log.info(f"   Cycle Score: {cycle_score:.1f}/100")

            # ── Rare Cycle Setup ───────────────────────────────────────────
            rare_setup = _detect_rare_cycle_setup(
                phase, halving_data, onchain_data, volatility_data, macro_data
            )
            if rare_setup["active"]:
                log.info(f"   ★ RARE CYCLE SETUP: {rare_setup['setup_type']} — {rare_setup['description']}")

            # ── Monta output ───────────────────────────────────────────────
            result = _build_output(
                phase, cycle_score,
                trend_data, halving_data, onchain_data, volatility_data,
                macro_data, rare_setup,
            )

            elapsed = time.time() - t0
            log.info(
                f"   Cycle engine: phase={result['cycle_phase']} | "
                f"score={result['cycle_score']:.1f} | "
                f"bias={result['bias_adjustment']} | "
                f"risk_mult={result['risk_multiplier']}x | "
                f"rare={result['rare_cycle_setup']} | "
                f"({elapsed:.1f}s)"
            )

            _cycle_cache    = result
            _cycle_cache_ts = time.time()
            return result

        except Exception as e:
            log.warning(f"   Cycle engine falhou: {type(e).__name__}: {e}")
            return _neutral_output(f"erro: {e}")


def _neutral_output(reason: str = "") -> dict:
    """Output neutro quando não há dados ou ocorre erro."""
    return {
        "cycle_phase":        "ACCUMULATION",
        "cycle_confidence":    0.0,
        "cycle_strength":     "WEAK",
        "cycle_score":        50.0,
        "risk_level":         "MEDIUM",
        "risk_multiplier":    1.0,
        "bias_adjustment":    "NEUTRAL",
        "macro_trend":        "NEUTRAL",
        "macro_score":        50.0,
        "cycle_position_pct": 50.0,
        "expected_volatility":"MEDIUM",
        "rare_cycle_setup":   False,
        "rare_cycle_type":    None,
        "rare_cycle_desc":    None,
        "phase_description":  reason or "Dados insuficientes",
        "active_signals":     [],
        "halving":    {},
        "trend":      {},
        "onchain":    {},
        "volatility": {},
    }


# ─── Singleton ────────────────────────────────────────────────────────────────

_engine_instance: Optional[BitcoinCycleEngine] = None


def run_bitcoin_cycle_analysis(
    force:            bool = False,
    external_onchain: Optional[dict] = None,
) -> dict:
    """
    Entry point singleton para o Bitcoin Cycle Engine.

    Uso:
        from bitcoin_cycle_engine import run_bitcoin_cycle_analysis
        cycle = run_bitcoin_cycle_analysis()
    """
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = BitcoinCycleEngine()
    return _engine_instance.run(force=force, external_onchain=external_onchain)
