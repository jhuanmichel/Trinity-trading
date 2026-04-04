"""
cycle_scoring.py — Scoring de Ciclo Bitcoin
Trinity Trading | Bitcoin Cycle Engine

Fórmula:
  cycle_score = (
    trend_strength     × 0.30 +
    volatility_score   × 0.20 +
    halving_position   × 0.15 +
    momentum           × 0.15 +
    volume_expansion   × 0.10 +
    drawdown_recovery  × 0.10
  )
  Normalizado: 0-100
"""

from .cycle_classifier import CyclePhase, RISK_MULTIPLIERS

# Pesos do cycle score
CYCLE_WEIGHTS = {
    "trend_strength":    0.30,
    "volatility_score":  0.20,
    "halving_position":  0.15,
    "momentum":          0.15,
    "volume_expansion":  0.10,
    "drawdown_recovery": 0.10,
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _momentum_score(trend_data: dict, onchain_data: dict) -> float:
    """
    Proxy de momentum macro: combina trend_strength com onchain_strength.
    Retorna 0-100.
    """
    ts = float(trend_data.get("trend_strength",   50.0))
    oc = float(onchain_data.get("onchain_strength", 50.0))
    return ts * 0.6 + oc * 0.4


def _volume_expansion_score(volatility_data: dict) -> float:
    """Score de expansão de volume/volatilidade. Retorna 0-100."""
    vol_exp = volatility_data.get("volume_expansion", False)
    atr_exp = volatility_data.get("atr_expansion",    False)
    vol_ph  = volatility_data.get("volatility_phase", "MEDIUM")

    score = 50.0
    if vol_exp: score += 30
    if atr_exp: score += 15
    # Alta volatilidade em contexto neutro/bearish penaliza levemente
    if vol_ph == "HIGH": score -= 10

    return round(min(100.0, max(0.0, score)), 1)


# ─── Run ─────────────────────────────────────────────────────────────────────

def calculate_cycle_score(
    trend_data:      dict,
    halving_data:    dict,
    onchain_data:    dict,
    volatility_data: dict,
) -> float:
    """
    Calcula o cycle_score final (0-100).

    Returns:
        float 0-100
    """
    trend_strength    = float(trend_data.get("trend_strength",   50.0))
    volatility_score  = float(volatility_data.get("volatility_score", 50.0))
    halving_position  = float(halving_data.get("halving_score",   50.0))
    momentum          = _momentum_score(trend_data, onchain_data)
    volume_expansion  = _volume_expansion_score(volatility_data)
    drawdown_recovery = float(trend_data.get("drawdown_score",   50.0))

    raw = (
        trend_strength    * CYCLE_WEIGHTS["trend_strength"]    +
        volatility_score  * CYCLE_WEIGHTS["volatility_score"]  +
        halving_position  * CYCLE_WEIGHTS["halving_position"]  +
        momentum          * CYCLE_WEIGHTS["momentum"]          +
        volume_expansion  * CYCLE_WEIGHTS["volume_expansion"]  +
        drawdown_recovery * CYCLE_WEIGHTS["drawdown_recovery"]
    )

    return round(max(0.0, min(100.0, raw)), 1)


def get_risk_multiplier(phase: CyclePhase) -> float:
    """Retorna o multiplicador de risco para a fase do ciclo."""
    return RISK_MULTIPLIERS.get(phase, 1.0)


def get_bias_adjustment(phase: CyclePhase, macro_score: float) -> str:
    """
    Determina o ajuste de bias para trading com base na fase do ciclo.

    Returns:
        "AGGRESSIVE_LONG" | "FAVOR_LONG" | "NEUTRAL" | "REDUCE_LONGS" | "FAVOR_SHORT"
    """
    if phase == CyclePhase.MID_BULL and macro_score >= 60:
        return "AGGRESSIVE_LONG"

    if phase == CyclePhase.EARLY_BULL:
        return "FAVOR_LONG"

    if phase == CyclePhase.ACCUMULATION:
        return "FAVOR_LONG"

    if phase == CyclePhase.LATE_BULL:
        return "REDUCE_LONGS"

    if phase in (CyclePhase.DISTRIBUTION, CyclePhase.BEAR, CyclePhase.CAPITULATION):
        return "FAVOR_SHORT"

    return "NEUTRAL"
