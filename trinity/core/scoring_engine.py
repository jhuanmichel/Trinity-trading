"""
scoring_engine.py — Trinity Score v7: Scoring Dinâmico e Ponderado

Calcula o Trinity Score final [0-100] onde:
  50 = mercado neutro (sem viés direcional)
  > 50 = território bullish (quanto maior, mais forte o sinal de compra)
  < 50 = território bearish (quanto menor, mais forte o sinal de venda)

Fórmula:
  Trinity Score v7 = Σ (engine_score_i × weight_i)

Propriedades:
  - Todos os scores de engine são [0-100] com 50 = neutro (ver engine_orchestrator.py)
  - Pesos somam 1.0 (garantido pelo normalizer)
  - Baseline com mercado 100% neutro = 50.0 ✓
  - Pesos adaptativos do Meta Engine substituem pesos padrão quando disponíveis
  - Modificadores de regime ajustam score em mercados extremos

Saída:
  {
    "trinity_score":        0-100,
    "score_direction":      BULLISH | BEARISH | NEUTRAL,
    "score_strength":       STRONG | MODERATE | WEAK | NEUTRAL,
    "engine_contributions": {engine_id: contribution},
    "top_contributors":     [(engine_id, contribution), ...],
    "regime_modifier":      float,  # ajuste aplicado por condições de mercado
    "weights_used":         {engine_id: float},
  }
"""
import logging
from typing import Dict, List, Tuple

from trinity.core.engine_orchestrator import EngineResult

log = logging.getLogger(__name__)

# ── Limiares de força do sinal ────────────────────────────────────────────────
BULLISH_STRONG_THR  = 72.0   # score ≥ 72 = STRONG BULLISH
BULLISH_MODERATE_THR = 62.0  # score ≥ 62 = MODERATE BULLISH
BEARISH_MODERATE_THR = 38.0  # score ≤ 38 = MODERATE BEARISH
BEARISH_STRONG_THR  = 28.0   # score ≤ 28 = STRONG BEARISH
# 38-62 = NEUTRAL zone

# Modificadores de regime de mercado
REGIME_MODIFIERS = {
    "TRENDING_UP":          1.08,   # amplifica em tendência de alta
    "TRENDING_DOWN":        1.08,   # amplifica em tendência de baixa
    "TRENDING":             1.08,   # compat retroativa (pode aparecer em logs antigos)
    "RANGING":              0.92,   # atenua em lateral
    "REVERSAL":             0.88,   # máxima cautela em reversão
    "ACCUMULATION":         1.04,
    "DISTRIBUTION":         1.04,
    "VOLATILITY_EXPANSION": 0.85,   # muito perigoso, atenua
    "MANIPULATION":         0.75,   # ignora quase tudo
}


def calculate_score(
    engine_results: Dict[str, EngineResult],
    market_regime: str = "RANGING",
    conflict_score: float = 0.0,
) -> dict:
    """
    Calcula Trinity Score v7.

    Args:
        engine_results:  dict[engine_id, EngineResult]
        market_regime:   regime atual de mercado (do Neural Engine ou Regime indicator)
        conflict_score:  [0-100] conflito detectado (penaliza score extremo)

    Returns:
        dict com trinity_score e metadados
    """
    # ── Calcula contribuições por engine ─────────────────────────────────────
    contributions: Dict[str, float] = {}
    total_weight = 0.0
    weighted_sum = 0.0

    for eng_id, result in engine_results.items():
        w = result.weight
        s = result.score  # [0, 100], 50 = neutro

        contribution = s * w
        contributions[eng_id] = round(contribution, 4)

        weighted_sum  += contribution
        total_weight  += w

    # Normaliza se pesos não somam exatamente 1.0
    if total_weight > 0:
        raw_score = weighted_sum / total_weight
    else:
        raw_score = 50.0

    # ── Modificador de regime ─────────────────────────────────────────────────
    regime_mod = REGIME_MODIFIERS.get(market_regime, 1.0)
    # Aplica modificador somente ao desvio do neutro (50)
    deviation   = raw_score - 50.0
    raw_score   = 50.0 + deviation * regime_mod

    # ── Penalidade por conflito ───────────────────────────────────────────────
    # Alto conflito empurra score em direção ao neutro (50)
    if conflict_score > 0:
        conflict_factor = 1.0 - (conflict_score / 100.0) * 0.40
        deviation_after_regime = raw_score - 50.0
        raw_score = 50.0 + deviation_after_regime * conflict_factor

    trinity_score = max(0.0, min(100.0, raw_score))
    trinity_score = round(trinity_score, 1)

    # ── Classificação direcional e de força ───────────────────────────────────
    if trinity_score >= BULLISH_STRONG_THR:
        score_direction = "BULLISH"
        score_strength  = "STRONG"
    elif trinity_score >= BULLISH_MODERATE_THR:
        score_direction = "BULLISH"
        score_strength  = "MODERATE"
    elif trinity_score <= BEARISH_STRONG_THR:
        score_direction = "BEARISH"
        score_strength  = "STRONG"
    elif trinity_score <= BEARISH_MODERATE_THR:
        score_direction = "BEARISH"
        score_strength  = "MODERATE"
    elif abs(trinity_score - 50.0) >= 5.0:
        score_direction = "BULLISH" if trinity_score > 50 else "BEARISH"
        score_strength  = "WEAK"
    else:
        score_direction = "NEUTRAL"
        score_strength  = "NEUTRAL"

    # ── Top contributors ──────────────────────────────────────────────────────
    # Ordenado por desvio do neutro (impacto direcional)
    top_contributors: List[Tuple[str, float]] = sorted(
        [(eng_id, abs(contr - engine_results[eng_id].weight * 50))
         for eng_id, contr in contributions.items()],
        key=lambda x: x[1],
        reverse=True,
    )[:4]

    weights_used = {eng_id: round(r.weight, 4) for eng_id, r in engine_results.items()}

    log.debug(
        f"   [Scoring] raw={raw_score:.1f} → final={trinity_score:.1f} | "
        f"dir={score_direction} str={score_strength} | "
        f"regime={market_regime}({regime_mod:.2f}) conflict_pen={conflict_score:.0f}"
    )

    return {
        "trinity_score":        trinity_score,
        "score_direction":      score_direction,
        "score_strength":       score_strength,
        "engine_contributions": contributions,
        "top_contributors":     [(e, round(v, 3)) for e, v in top_contributors],
        "regime_modifier":      round(regime_mod, 3),
        "weights_used":         weights_used,
        "raw_score_premod":     round(weighted_sum / total_weight if total_weight else 50.0, 2),
    }
