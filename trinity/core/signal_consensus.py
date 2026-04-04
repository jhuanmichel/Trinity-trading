"""
signal_consensus.py — Motor de Consenso Direcional

Analisa as saídas dos 10 engines e calcula:
  - Votos BULLISH / BEARISH / NEUTRAL (ponderados por peso e confiança)
  - Consenso dominante
  - Percentual de concordância por lado
  - Engines que contribuíram para cada lado
  - Confidence ajustado pelo grau de concordância

Dois modos de votação:
  1. Votação simples:    1 voto por engine (apenas válidos)
  2. Votação ponderada: peso × confidence como voto fracionário (default)

Saída:
  {
    "consensus":       BULLISH | BEARISH | NEUTRAL,
    "bullish_count":   int,
    "bearish_count":   int,
    "neutral_count":   int,
    "total_engines":   int,
    "valid_engines":   int,
    "confidence":      0-100,
    "bull_pct":        0-100,
    "bear_pct":        0-100,
    "neutral_pct":     0-100,
    "dominant_pct":    0-100,  # % do lado dominante
    "weighted_bull":   float,  # voto ponderado total bullish
    "weighted_bear":   float,  # voto ponderado total bearish
    "bullish_engines": [engine_id, ...],
    "bearish_engines": [engine_id, ...],
    "neutral_engines": [engine_id, ...],
    "high_weight_consensus": bool,  # engines de alto peso (>0.12) concordam?
    "raw_votes":       {engine_id: direction},
  }
"""
import logging
from typing import Dict, List

from trinity.core.engine_orchestrator import EngineResult

log = logging.getLogger(__name__)

# Engines considerados de "alto peso" (decisivos para consenso de qualidade)
HIGH_WEIGHT_ENGINES = {"smart_money", "direction", "neural", "pressure"}

# Limiar de dominância para consenso significativo
CONSENSUS_THRESHOLD  = 0.55   # 55% dos votos ponderados para consensus
STRONG_CONSENSUS_THR = 0.72   # 72% para "consenso forte"


def calculate_consensus(
    engine_results: Dict[str, EngineResult],
    use_weighted: bool = True,
) -> dict:
    """
    Calcula consenso direcional entre todos os engines.

    Args:
        engine_results: dict[engine_id, EngineResult]
        use_weighted:   se True, pondera por weight × confidence / 100

    Returns:
        dict completo de consenso (ver docstring do módulo)
    """
    bullish_engines: List[str] = []
    bearish_engines: List[str] = []
    neutral_engines: List[str] = []

    raw_votes:     dict = {}
    weighted_bull: float = 0.0
    weighted_bear: float = 0.0
    weighted_neut: float = 0.0
    total_weight:  float = 0.0

    bull_count = bear_count = neut_count = valid_count = 0

    for eng_id, result in engine_results.items():
        raw_votes[eng_id] = result.direction

        if not result.valid:
            neutral_engines.append(eng_id)
            neut_count += 1
            continue

        valid_count += 1

        # Voto fracionário: peso × (confiança / 100)
        vote_weight = result.weight * (result.confidence / 100.0) if use_weighted else result.weight

        if result.direction == "BULLISH":
            bullish_engines.append(eng_id)
            weighted_bull += vote_weight
            bull_count    += 1
        elif result.direction == "BEARISH":
            bearish_engines.append(eng_id)
            weighted_bear += vote_weight
            bear_count    += 1
        else:
            neutral_engines.append(eng_id)
            weighted_neut += vote_weight
            neut_count    += 1

        total_weight += vote_weight

    # Normaliza votos ponderados
    if total_weight > 0:
        bull_pct  = (weighted_bull / total_weight) * 100
        bear_pct  = (weighted_bear / total_weight) * 100
        neut_pct  = (weighted_neut / total_weight) * 100
    else:
        bull_pct = bear_pct = neut_pct = 0.0

    # Determina consenso dominante
    if bull_pct >= bear_pct and bull_pct >= neut_pct:
        if bull_pct / 100 >= CONSENSUS_THRESHOLD:
            consensus    = "BULLISH"
            dominant_pct = bull_pct
        else:
            consensus    = "NEUTRAL"
            dominant_pct = bull_pct
    elif bear_pct > bull_pct and bear_pct >= neut_pct:
        if bear_pct / 100 >= CONSENSUS_THRESHOLD:
            consensus    = "BEARISH"
            dominant_pct = bear_pct
        else:
            consensus    = "NEUTRAL"
            dominant_pct = bear_pct
    else:
        consensus    = "NEUTRAL"
        dominant_pct = neut_pct

    # Confidence do consenso: proporcional à dominância e à concordância
    # Fórmula: dominant_pct × alinhamento_high_weight
    hw_consensus  = _check_high_weight_alignment(engine_results, consensus)
    hw_multiplier = 1.15 if hw_consensus else 0.85
    raw_confidence = dominant_pct * hw_multiplier

    # Penaliza consenso fraco (< CONSENSUS_THRESHOLD)
    if dominant_pct / 100 < CONSENSUS_THRESHOLD:
        raw_confidence *= 0.70

    consensus_confidence = max(0.0, min(95.0, raw_confidence))

    total_engines = len(engine_results)
    result_dict   = {
        "consensus":             consensus,
        "bullish_count":         bull_count,
        "bearish_count":         bear_count,
        "neutral_count":         neut_count,
        "total_engines":         total_engines,
        "valid_engines":         valid_count,
        "confidence":            round(consensus_confidence, 1),
        "bull_pct":              round(bull_pct, 1),
        "bear_pct":              round(bear_pct, 1),
        "neutral_pct":           round(neut_pct, 1),
        "dominant_pct":          round(dominant_pct, 1),
        "weighted_bull":         round(weighted_bull, 4),
        "weighted_bear":         round(weighted_bear, 4),
        "bullish_engines":       bullish_engines,
        "bearish_engines":       bearish_engines,
        "neutral_engines":       neutral_engines,
        "high_weight_consensus": hw_consensus,
        "strong_consensus":      dominant_pct / 100 >= STRONG_CONSENSUS_THR,
        "raw_votes":             raw_votes,
    }

    log.debug(
        f"   [Consensus] {consensus} | bull={bull_pct:.0f}% bear={bear_pct:.0f}% "
        f"neut={neut_pct:.0f}% | valid={valid_count}/{total_engines} | "
        f"hw_ok={hw_consensus} | conf={consensus_confidence:.0f}"
    )

    return result_dict


def _check_high_weight_alignment(
    engine_results: Dict[str, EngineResult],
    consensus: str,
) -> bool:
    """
    Verifica se engines de alto peso estão alinhados com o consenso.

    Retorna True se a maioria dos HIGH_WEIGHT_ENGINES concorda com consensus.
    """
    if consensus == "NEUTRAL":
        return True  # neutro é sempre "alinhado" com neutro

    aligned   = 0
    available = 0

    for eng_id in HIGH_WEIGHT_ENGINES:
        result = engine_results.get(eng_id)
        if result and result.valid:
            available += 1
            if result.direction == consensus:
                aligned += 1

    if available == 0:
        return False

    return (aligned / available) >= 0.50
