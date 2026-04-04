"""
signal_generator.py — Gerador de Sinal Final Institucional

Produz o sinal de trade final (LONG | SHORT | NEUTRAL) e seu nível de risco
combinando todas as saídas do pipeline Trinity Core.

Critérios de sinal:

  LONG (STRONG):
    trinity_score ≥ 72 AND consensus == BULLISH AND conflict == LOW
    AND confidence ≥ 72 AND signal_quality == HIGH

  LONG (MODERATE):
    trinity_score ≥ 62 AND consensus == BULLISH AND conflict != HIGH
    AND confidence ≥ 55

  SHORT (STRONG):
    trinity_score ≤ 28 AND consensus == BEARISH AND conflict == LOW
    AND confidence ≥ 72 AND signal_quality == HIGH

  SHORT (MODERATE):
    trinity_score ≤ 38 AND consensus == BEARISH AND conflict != HIGH
    AND confidence ≥ 55

  NEUTRAL:
    qualquer outra condição

Níveis de risco:
  LOW:    confidence ≥ 78 AND signal_quality == HIGH AND conflict == LOW
  MEDIUM: confidence ≥ 58 AND signal_quality != LOW  AND conflict != HIGH
  HIGH:   else

Saída:
  {
    "signal":            LONG | SHORT | NEUTRAL,
    "signal_strength":   STRONG | MODERATE | WEAK,
    "confidence":        0-100,
    "risk_level":        LOW | MEDIUM | HIGH,
    "trinity_score":     0-100,
    "market_bias":       BULLISH | BEARISH | NEUTRAL,
    "trade_allowed":     bool,
    "position_size_factor": 0-1.0,
    "signal_rationale":  str,
    "alert_priority":    CRITICAL | HIGH | MEDIUM | LOW,
  }
"""
import logging
from typing import Dict

from trinity.core.engine_orchestrator import EngineResult

log = logging.getLogger(__name__)

# ── Limiares ──────────────────────────────────────────────────────────────────
LONG_STRONG_THR    = 72.0
LONG_MODERATE_THR  = 62.0
SHORT_MODERATE_THR = 38.0
SHORT_STRONG_THR   = 28.0

CONF_HIGH_THR   = 72.0
CONF_MEDIUM_THR = 55.0

RISK_LOW_THR    = 78.0
RISK_MEDIUM_THR = 58.0


def generate_signal(
    scoring_data:    dict,
    consensus_data:  dict,
    conflict_data:   dict,
    confidence_data: dict,
) -> dict:
    """
    Gera o sinal final de trade do Trinity Core.

    Args:
        scoring_data:    saída do ScoringEngine
        consensus_data:  saída do SignalConsensus
        conflict_data:   saída do ConflictDetector
        confidence_data: saída do ConfidenceEngine

    Returns:
        dict com sinal final e metadados (ver docstring do módulo)
    """
    trinity_score  = scoring_data.get("trinity_score",   50.0)
    score_strength = scoring_data.get("score_strength",  "NEUTRAL")
    consensus      = consensus_data.get("consensus",     "NEUTRAL")
    conflict_level = conflict_data.get("conflict_level", "HIGH")
    trade_allowed  = conflict_data.get("trade_allowed",  False)
    confidence     = confidence_data.get("confidence",   0.0)
    signal_quality = confidence_data.get("signal_quality", "LOW")

    # ── Determina sinal e força ───────────────────────────────────────────────
    signal, signal_strength = _determine_signal(
        trinity_score, consensus, conflict_level, confidence, signal_quality, trade_allowed
    )

    # ── Nível de risco ────────────────────────────────────────────────────────
    risk_level = _determine_risk(confidence, signal_quality, conflict_level, signal)

    # ── Fator de tamanho de posição ───────────────────────────────────────────
    position_factor = _calc_position_factor(signal_strength, risk_level, conflict_data)

    # ── Prioridade de alerta ──────────────────────────────────────────────────
    alert_priority = _determine_alert_priority(signal, signal_strength, confidence, trinity_score)

    # ── Market bias (direção de mercado, independente do sinal) ──────────────
    if trinity_score >= LONG_MODERATE_THR:
        market_bias = "BULLISH"
    elif trinity_score <= SHORT_MODERATE_THR:
        market_bias = "BEARISH"
    else:
        market_bias = "NEUTRAL"

    # ── Rationale ─────────────────────────────────────────────────────────────
    rationale = _build_rationale(
        signal, signal_strength, trinity_score, consensus,
        conflict_level, confidence, signal_quality,
        scoring_data, consensus_data, conflict_data,
    )

    log.info(
        f"   [Signal] {signal} ({signal_strength}) | "
        f"trinity={trinity_score:.1f} | conf={confidence:.0f}% | "
        f"risk={risk_level} | quality={signal_quality} | "
        f"priority={alert_priority}"
    )

    return {
        "signal":               signal,
        "signal_strength":      signal_strength,
        "confidence":           round(confidence, 1),
        "risk_level":           risk_level,
        "trinity_score":        trinity_score,
        "market_bias":          market_bias,
        "trade_allowed":        trade_allowed and signal != "NEUTRAL",
        "position_size_factor": position_factor,
        "signal_rationale":     rationale,
        "alert_priority":       alert_priority,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _determine_signal(
    score: float, consensus: str, conflict: str,
    conf: float, quality: str, trade_allowed: bool,
) -> tuple:
    """Retorna (signal, signal_strength)."""

    if not trade_allowed:
        return "NEUTRAL", "NEUTRAL"

    # ── LONG ──────────────────────────────────────────────────────────────────
    if consensus == "BULLISH":
        if (score >= LONG_STRONG_THR
                and conflict == "LOW"
                and conf >= CONF_HIGH_THR
                and quality == "HIGH"):
            return "LONG", "STRONG"
        if (score >= LONG_MODERATE_THR
                and conflict != "HIGH"
                and conf >= CONF_MEDIUM_THR):
            return "LONG", "MODERATE"
        if score >= LONG_MODERATE_THR and conf >= 45.0:
            return "LONG", "WEAK"

    # ── SHORT ─────────────────────────────────────────────────────────────────
    elif consensus == "BEARISH":
        if (score <= SHORT_STRONG_THR
                and conflict == "LOW"
                and conf >= CONF_HIGH_THR
                and quality == "HIGH"):
            return "SHORT", "STRONG"
        if (score <= SHORT_MODERATE_THR
                and conflict != "HIGH"
                and conf >= CONF_MEDIUM_THR):
            return "SHORT", "MODERATE"
        if score <= SHORT_MODERATE_THR and conf >= 45.0:
            return "SHORT", "WEAK"

    return "NEUTRAL", "NEUTRAL"


def _determine_risk(
    conf: float, quality: str, conflict: str, signal: str,
) -> str:
    """Determina nível de risco operacional."""
    if signal == "NEUTRAL":
        return "HIGH"

    if conf >= RISK_LOW_THR and quality == "HIGH" and conflict == "LOW":
        return "LOW"
    if conf >= RISK_MEDIUM_THR and quality != "LOW" and conflict != "HIGH":
        return "MEDIUM"
    return "HIGH"


def _calc_position_factor(
    signal_strength: str,
    risk_level: str,
    conflict_data: dict,
) -> float:
    """Fator de tamanho de posição [0, 1.0]."""
    base = conflict_data.get("trade_size_factor", 1.0)

    strength_map = {
        "STRONG":  1.00,
        "MODERATE": 0.75,
        "WEAK":    0.50,
        "NEUTRAL": 0.00,
    }
    risk_map = {"LOW": 1.00, "MEDIUM": 0.80, "HIGH": 0.50}

    factor = base * strength_map.get(signal_strength, 0.0) * risk_map.get(risk_level, 0.5)
    return round(max(0.0, min(1.0, factor)), 2)


def _determine_alert_priority(
    signal: str, strength: str, conf: float, score: float,
) -> str:
    """Prioridade do alerta para envio ao Telegram."""
    if signal == "NEUTRAL":
        return "LOW"
    if strength == "STRONG" and conf >= 75:
        return "CRITICAL"
    if strength in ("STRONG", "MODERATE") and conf >= 60:
        return "HIGH"
    if signal != "NEUTRAL":
        return "MEDIUM"
    return "LOW"


def _build_rationale(
    signal: str, strength: str, score: float, consensus: str,
    conflict: str, conf: float, quality: str,
    scoring_data: dict, consensus_data: dict, conflict_data: dict,
) -> str:
    """Texto explicativo do sinal gerado."""
    if signal == "NEUTRAL":
        reasons = []
        if conflict == "HIGH":
            reasons.append(f"conflito severo ({conflict_data.get('pair_count',0)} pares críticos)")
        if score > SHORT_MODERATE_THR and score < LONG_MODERATE_THR:
            reasons.append(f"Trinity Score em zona neutra ({score:.0f})")
        if consensus == "NEUTRAL":
            reasons.append("sem consenso direcional")
        if conf < CONF_MEDIUM_THR:
            reasons.append(f"confiança insuficiente ({conf:.0f}%)")
        return "NEUTRAL — " + (" | ".join(reasons) if reasons else "mercado sem viés")

    top = scoring_data.get("top_contributors", [])
    top_str = ", ".join(f"{e}({v:.2f})" for e, v in top[:3]) if top else "N/A"

    bull_n = consensus_data.get("bullish_count", 0)
    bear_n = consensus_data.get("bearish_count", 0)
    dom_pct = consensus_data.get("dominant_pct", 0)

    return (
        f"{signal} {strength} | Score: {score:.1f} | "
        f"Consenso: {bull_n}🟢 vs {bear_n}🔴 ({dom_pct:.0f}% dominante) | "
        f"Conf: {conf:.0f}% | Quality: {quality} | "
        f"Top: {top_str}"
    )
