"""
macro_scoring.py — Modelos de Risco e Liquidez Macro
Trinity Trading | Geopolitical Engine

Dois modelos:
  1. Risk Sentiment Model → RISK_ON | RISK_OFF | NEUTRAL
  2. Liquidity Model      → INCREASING | DECREASING | NEUTRAL

Inputs: artigos classificados + market_score do sentiment_engine

Geo Score final (0-100) exportado para Trinity Score v2:
  geo_score = market_score × risk_multiplier × liquidity_multiplier
"""

from typing import Tuple

# ─── Limiares ────────────────────────────────────────────────────────────────

RISK_ON_THRESHOLD  = 60   # market_score >= 60 → RISK_ON
RISK_OFF_THRESHOLD = 40   # market_score <= 40 → RISK_OFF

LIQ_INCREASE_THRESHOLD = 62  # market_score >= 62 → INCREASING
LIQ_DECREASE_THRESHOLD = 38  # market_score <= 38 → DECREASING

# Multiplicadores de ajuste do geo_score
RISK_MULTIPLIERS = {
    "RISK_ON":  1.10,   # mercado em apetite de risco → amplifica bullish
    "RISK_OFF": 0.90,   # mercado em aversão ao risco → atenua signal
    "NEUTRAL":  1.00,
}

LIQUIDITY_MULTIPLIERS = {
    "INCREASING": 1.08,   # liquidez entrando = bullish macro
    "DECREASING": 0.93,   # liquidez saindo  = bearish macro
    "NEUTRAL":    1.00,
}


# ─── Keywords para risk/liquidity model ──────────────────────────────────────

RISK_ON_TRIGGERS = {
    # Eventos que aceleram apetite de risco
    "rate cut", "rate cuts", "fed pivot", "quantitative easing",
    "stimulus", "dovish", "liquidity injection", "etf approved",
    "bitcoin reserve", "institutional adoption", "credit easing",
    "bank rescue", "bailout", "emergency facility",
}

RISK_OFF_TRIGGERS = {
    # Eventos que aumentam aversão a risco
    "rate hike", "rate hikes", "hawkish", "quantitative tightening",
    "nuclear threat", "military strike", "war escalation",
    "exchange collapse", "ban crypto", "ban bitcoin",
    "market crash", "credit crunch", "bank failure",
    "regulatory crackdown", "sec lawsuit",
}

LIQUIDITY_INCREASE_TRIGGERS = {
    # Eventos que injetam liquidez
    "quantitative easing", "liquidity injection", "money printing",
    "stimulus", "balance sheet expan", "credit easing",
    "rate cut", "rate cuts", "accommodative", "dovish",
    "bank rescue", "emergency facility", "bailout",
}

LIQUIDITY_DECREASE_TRIGGERS = {
    # Eventos que drenam liquidez
    "quantitative tightening", "balance sheet reduction",
    "liquidity drain", "rate hike", "rate hikes",
    "tightening monetary", "restrictive policy", "credit crunch",
    "higher for longer", "hawkish",
}


# ─── Funções de modelo ────────────────────────────────────────────────────────

def _count_triggers(articles: list, trigger_set: set) -> int:
    """Conta quantos artigos de alto impacto contêm triggers do conjunto."""
    count = 0
    for art in articles:
        trigger = art.get("detected_trigger", "")
        if trigger in trigger_set:
            count += 1
    return count


def assess_risk_sentiment(
    market_score: float,
    classified_articles: list,
) -> Tuple[str, float]:
    """
    Determina o regime de risco (RISK_ON | RISK_OFF | NEUTRAL).

    Prioridade:
      1. Triggers de alto impacto nas notícias
      2. market_score como fallback

    Returns:
        (risk_state: str, confidence: float 0-1)
    """
    risk_on_count  = _count_triggers(classified_articles, RISK_ON_TRIGGERS)
    risk_off_count = _count_triggers(classified_articles, RISK_OFF_TRIGGERS)

    if risk_on_count > risk_off_count and risk_on_count >= 2:
        return ("RISK_ON", min(1.0, risk_on_count / 5.0))

    if risk_off_count > risk_on_count and risk_off_count >= 2:
        return ("RISK_OFF", min(1.0, risk_off_count / 5.0))

    # Fallback: market_score
    if market_score >= RISK_ON_THRESHOLD:
        return ("RISK_ON", 0.5)
    elif market_score <= RISK_OFF_THRESHOLD:
        return ("RISK_OFF", 0.5)

    return ("NEUTRAL", 0.4)


def assess_liquidity_model(
    market_score: float,
    classified_articles: list,
) -> Tuple[str, float]:
    """
    Determina o estado de liquidez macro (INCREASING | DECREASING | NEUTRAL).

    Returns:
        (liquidity_state: str, confidence: float 0-1)
    """
    inc_count = _count_triggers(classified_articles, LIQUIDITY_INCREASE_TRIGGERS)
    dec_count = _count_triggers(classified_articles, LIQUIDITY_DECREASE_TRIGGERS)

    if inc_count > dec_count and inc_count >= 2:
        return ("INCREASING", min(1.0, inc_count / 5.0))

    if dec_count > inc_count and dec_count >= 2:
        return ("DECREASING", min(1.0, dec_count / 5.0))

    # Fallback: market_score
    if market_score >= LIQ_INCREASE_THRESHOLD:
        return ("INCREASING", 0.4)
    elif market_score <= LIQ_DECREASE_THRESHOLD:
        return ("DECREASING", 0.4)

    return ("NEUTRAL", 0.3)


# ─── Geo Score Final ──────────────────────────────────────────────────────────

def calculate_geo_score(
    market_score:       float,
    risk_state:         str,
    liquidity_state:    str,
    confidence:         float,
) -> float:
    """
    Calcula o geo_score final (0-100) para uso no Trinity Score v2.

    geo_score = market_score × risk_multiplier × liquidity_multiplier × confidence_factor

    Nota: market_score já está em 0-100; multiplicadores ajustam levemente (+/-10%).
    confidence_factor: [0.5, 1.0] — evita scores muito altos com poucos artigos.
    """
    risk_mult = RISK_MULTIPLIERS.get(risk_state, 1.0)
    liq_mult  = LIQUIDITY_MULTIPLIERS.get(liquidity_state, 1.0)
    conf_factor = max(0.5, min(1.0, confidence))

    raw_score = market_score * risk_mult * liq_mult * conf_factor
    return max(0.0, min(100.0, round(raw_score, 1)))


# ─── Pipeline completo de macro scoring ──────────────────────────────────────

def run_macro_scoring(
    market_score:       float,
    confidence:         float,
    classified_articles: list,
) -> dict:
    """
    Pipeline completo: avalia risco, liquidez, calcula geo_score.

    Args:
        market_score:          score agregado do sentiment_engine (0-100)
        confidence:            confiança do sentiment_engine (0-1)
        classified_articles:   lista de artigos classificados

    Returns:
        {
            "geo_score":         float 0-100,
            "risk_sentiment":    "RISK_ON" | "RISK_OFF" | "NEUTRAL",
            "risk_confidence":   float 0-1,
            "liquidity_outlook": "INCREASING" | "DECREASING" | "NEUTRAL",
            "liq_confidence":    float 0-1,
            "geo_direction":     "BULLISH" | "BEARISH" | "NEUTRAL",
        }
    """
    risk_state,  risk_conf = assess_risk_sentiment(market_score, classified_articles)
    liq_state,   liq_conf  = assess_liquidity_model(market_score, classified_articles)

    combined_conf = (confidence + risk_conf + liq_conf) / 3.0
    geo_score = calculate_geo_score(market_score, risk_state, liq_state, combined_conf)

    # Direção geopolítica consolidada
    if geo_score >= 62:
        geo_direction = "BULLISH"
    elif geo_score <= 38:
        geo_direction = "BEARISH"
    else:
        geo_direction = "NEUTRAL"

    return {
        "geo_score":         geo_score,
        "risk_sentiment":    risk_state,
        "risk_confidence":   round(risk_conf, 2),
        "liquidity_outlook": liq_state,
        "liq_confidence":    round(liq_conf, 2),
        "geo_direction":     geo_direction,
    }
