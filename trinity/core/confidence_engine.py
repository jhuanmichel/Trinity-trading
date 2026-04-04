"""
confidence_engine.py — Motor de Confiança e Qualidade de Sinal

Calcula a confiança final [0-100] e a qualidade do sinal (LOW/MEDIUM/HIGH)
com base em múltiplos fatores:

Fatores (ponderados):
  1. Concordância (Agreement):    % de engines alinhados com consenso     25%
  2. Força do score:              distância do Trinity Score do neutro(50) 20%
  3. Confiança dos engines:       média ponderada de confidence dos engines 20%
  4. Alinhamento high-weight:     engines críticos (neural, dir, smc) ok?  20%
  5. Ausência de conflito:        quanto menor o conflito, melhor           15%

Signal Quality:
  HIGH:   confidence ≥ 72 AND conflict_level == LOW    AND score_strength STRONG|MODERATE
  MEDIUM: confidence ≥ 52 AND conflict_level != HIGH   AND score_strength != NEUTRAL
  LOW:    else

Saída:
  {
    "confidence":           0-100,
    "signal_quality":       LOW | MEDIUM | HIGH,
    "agreement_factor":     0-100,
    "strength_factor":      0-100,
    "engine_conf_factor":   0-100,
    "hw_alignment_factor":  0-100,
    "conflict_factor":      0-100,
    "is_tradeable":         bool,
    "quality_breakdown":    {factor: value},
    "degradation_reasons":  [str],
  }
"""
import logging
from typing import Dict, List

from trinity.core.engine_orchestrator import EngineResult

log = logging.getLogger(__name__)

# Pesos dos fatores de confiança
FACTOR_WEIGHTS = {
    "agreement":   0.25,
    "strength":    0.20,
    "engine_conf": 0.20,
    "hw_align":    0.20,
    "no_conflict": 0.15,
}

# Engines de alto peso (críticos para confiança)
HIGH_WEIGHT_ENGINES = {"smart_money", "direction", "neural", "pressure"}

# Limiares de qualidade
QUALITY_HIGH_THR   = 72.0
QUALITY_MEDIUM_THR = 52.0

# Score strength → factor de força
STRENGTH_FACTOR_MAP = {
    "STRONG":   90.0,
    "MODERATE": 68.0,
    "WEAK":     42.0,
    "NEUTRAL":  20.0,
}


def calculate_confidence(
    engine_results:  Dict[str, EngineResult],
    consensus_data:  dict,
    conflict_data:   dict,
    scoring_data:    dict,
) -> dict:
    """
    Calcula confiança e qualidade do sinal Trinity.

    Args:
        engine_results:  dict[engine_id, EngineResult]
        consensus_data:  saída do SignalConsensus
        conflict_data:   saída do ConflictDetector
        scoring_data:    saída do ScoringEngine

    Returns:
        dict com confidence e metadados de qualidade
    """
    degradation_reasons: List[str] = []

    # ── Fator 1: Concordância (agreement) ────────────────────────────────────
    dominant_pct   = consensus_data.get("dominant_pct", 50.0)
    # normaliza: 50% dominant = 0% agreement factor, 100% = 100% factor
    agreement_factor = max(0.0, (dominant_pct - 50.0) * 2.0)
    agreement_factor = min(100.0, agreement_factor)

    # Bonus se há consenso forte
    if consensus_data.get("strong_consensus", False):
        agreement_factor = min(100.0, agreement_factor * 1.15)

    # ── Fator 2: Força do Trinity Score ──────────────────────────────────────
    score_strength  = scoring_data.get("score_strength", "NEUTRAL")
    strength_factor = STRENGTH_FACTOR_MAP.get(score_strength, 20.0)

    trinity_score  = scoring_data.get("trinity_score", 50.0)
    score_dev      = abs(trinity_score - 50.0)
    # Bonus por desvio extremo do neutro
    if score_dev >= 20:
        strength_factor = min(100.0, strength_factor * 1.10)

    # ── Fator 3: Confiança média dos engines ──────────────────────────────────
    valid_results = [r for r in engine_results.values() if r.valid]
    if valid_results:
        total_conf = sum(r.confidence * r.weight for r in valid_results)
        total_w    = sum(r.weight for r in valid_results)
        avg_engine_conf = total_conf / total_w if total_w > 0 else 0.0
    else:
        avg_engine_conf = 0.0
        degradation_reasons.append("Nenhum engine válido")

    engine_conf_factor = min(100.0, avg_engine_conf)

    # ── Fator 4: Alinhamento dos engines de alto peso ─────────────────────────
    consensus      = consensus_data.get("consensus", "NEUTRAL")
    hw_factor      = _calc_hw_alignment(engine_results, consensus)

    # ── Fator 5: Ausência de conflito ─────────────────────────────────────────
    conflict_score = conflict_data.get("conflict_score", 0.0)
    no_conflict_factor = max(0.0, 100.0 - conflict_score)

    # ── Calcula confiança final ponderada ─────────────────────────────────────
    confidence_raw = (
        agreement_factor   * FACTOR_WEIGHTS["agreement"]   +
        strength_factor    * FACTOR_WEIGHTS["strength"]    +
        engine_conf_factor * FACTOR_WEIGHTS["engine_conf"] +
        hw_factor          * FACTOR_WEIGHTS["hw_align"]    +
        no_conflict_factor * FACTOR_WEIGHTS["no_conflict"]
    )

    # ── Penalidades adicionais ────────────────────────────────────────────────
    if conflict_data.get("conflict_level") == "HIGH":
        confidence_raw *= 0.60
        degradation_reasons.append("Conflito severo entre engines")

    valid_count = consensus_data.get("valid_engines", 0)
    if valid_count < 5:
        penalty = 1.0 - (5 - valid_count) * 0.08
        confidence_raw *= penalty
        degradation_reasons.append(f"Poucos engines válidos ({valid_count}/10)")

    # Consenso neutro penaliza confiança
    if consensus == "NEUTRAL":
        confidence_raw *= 0.55
        degradation_reasons.append("Consenso neutro — sem viés direcional")

    confidence = max(0.0, min(95.0, confidence_raw))
    confidence = round(confidence, 1)

    # ── Signal Quality ────────────────────────────────────────────────────────
    conflict_level = conflict_data.get("conflict_level", "HIGH")
    if (confidence >= QUALITY_HIGH_THR
            and conflict_level == "LOW"
            and score_strength in ("STRONG", "MODERATE")):
        signal_quality = "HIGH"
    elif (confidence >= QUALITY_MEDIUM_THR
            and conflict_level != "HIGH"
            and score_strength != "NEUTRAL"):
        signal_quality = "MEDIUM"
    else:
        signal_quality = "LOW"

    is_tradeable = signal_quality in ("HIGH", "MEDIUM") and conflict_data.get("trade_allowed", False)

    quality_breakdown = {
        "agreement":   round(agreement_factor, 1),
        "strength":    round(strength_factor, 1),
        "engine_conf": round(engine_conf_factor, 1),
        "hw_align":    round(hw_factor, 1),
        "no_conflict": round(no_conflict_factor, 1),
    }

    log.debug(
        f"   [Confidence] {confidence:.0f}% | quality={signal_quality} | "
        f"agree={agreement_factor:.0f} str={strength_factor:.0f} "
        f"eng_conf={engine_conf_factor:.0f} hw={hw_factor:.0f} "
        f"no_conf={no_conflict_factor:.0f} | tradeable={is_tradeable}"
    )

    return {
        "confidence":          confidence,
        "signal_quality":      signal_quality,
        "agreement_factor":    round(agreement_factor, 1),
        "strength_factor":     round(strength_factor, 1),
        "engine_conf_factor":  round(engine_conf_factor, 1),
        "hw_alignment_factor": round(hw_factor, 1),
        "conflict_factor":     round(no_conflict_factor, 1),
        "is_tradeable":        is_tradeable,
        "quality_breakdown":   quality_breakdown,
        "degradation_reasons": degradation_reasons,
    }


def _calc_hw_alignment(
    engine_results: Dict[str, EngineResult],
    consensus: str,
) -> float:
    """
    Score de alinhamento dos engines de alto peso [0-100].

    Retorna % de HIGH_WEIGHT_ENGINES que concordam com o consenso,
    ponderado pela confiança de cada um.
    """
    if consensus == "NEUTRAL":
        return 50.0  # neutro: alinhamento parcial por definição

    aligned_weight   = 0.0
    total_hw_weight  = 0.0

    for eng_id in HIGH_WEIGHT_ENGINES:
        result = engine_results.get(eng_id)
        if not result or not result.valid:
            continue
        w = result.weight * (result.confidence / 100.0)
        total_hw_weight += w
        if result.direction == consensus:
            aligned_weight += w

    if total_hw_weight == 0:
        return 50.0

    return min(100.0, (aligned_weight / total_hw_weight) * 100.0)
