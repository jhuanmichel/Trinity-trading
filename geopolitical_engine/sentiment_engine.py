"""
sentiment_engine.py — Motor de Sentimento Geopolítico
Trinity Trading | Geopolitical Engine

Calcula geo_score por artigo usando a fórmula ponderada:
  geo_score = sentiment×0.30 + impact×0.25 + relevance×0.20
            + macro_weight×0.15 + event_freq×0.10

Agregação para score final de mercado (0-100):
  - Agregação ponderada por source_weight + decaimento temporal
  - Outlier boost: evento alto impacto eleva o score global
"""

import math
from datetime import datetime, timezone
from typing import Optional

# ─── Pesos da fórmula de sentimento ──────────────────────────────────────────

GEO_WEIGHTS = {
    "sentiment":    0.30,
    "impact":       0.25,
    "relevance":    0.20,
    "macro_weight": 0.15,
    "event_freq":   0.10,
}

# Fator de decaimento temporal (horas)
# Após N horas, o peso de uma notícia decai pelo fator correspondente
TIME_DECAY_HALF_LIFE_HOURS = 6.0   # halve-life de 6h para SHORT, 24h para MID

# Mínimo de artigos para score confiável
MIN_ARTICLES_CONFIDENCE = 3

# Boost por evento de alto impacto (impact_score >= 85)
HIGH_IMPACT_BOOST = 15.0


# ─── Mapeamento direction → sentiment_raw ─────────────────────────────────────

def _direction_to_sentiment(direction: str, impact_score: float) -> float:
    """
    Converte direção BULLISH/BEARISH/NEUTRAL em valor 0-100 de sentimento.
    BULLISH = acima de 50, BEARISH = abaixo de 50, NEUTRAL = 50
    O impacto modula a intensidade do desvio.
    """
    if direction == "BULLISH":
        # 50 (neutro) + intensidade proporcional ao impacto
        return min(100.0, 50.0 + (impact_score * 0.5))
    elif direction == "BEARISH":
        return max(0.0, 50.0 - (impact_score * 0.5))
    return 50.0  # NEUTRAL


# ─── Decaimento temporal ──────────────────────────────────────────────────────

def _time_decay(published: str, time_horizon: str) -> float:
    """
    Retorna fator de peso 0-1 baseado na idade do artigo.
    SHORT news = decaimento rápido (6h half-life)
    MID news   = decaimento médio (24h half-life)
    LONG news  = decaimento lento (72h half-life)
    """
    half_life_map = {
        "SHORT": 6.0,
        "MID":   24.0,
        "LONG":  72.0,
    }
    half_life = half_life_map.get(time_horizon, 24.0)

    try:
        pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        now_dt = datetime.now(timezone.utc)
        age_hours = (now_dt - pub_dt).total_seconds() / 3600.0
        # Decaimento exponencial: e^(-λ * t), λ = ln(2) / half_life
        lam = math.log(2) / half_life
        return math.exp(-lam * age_hours)
    except Exception:
        return 0.5  # fallback: peso médio


# ─── Score individual por artigo ──────────────────────────────────────────────

def score_article(article: dict) -> dict:
    """
    Calcula geo_score para um artigo já classificado (output do macro_classifier).

    Retorna o artigo enriquecido com:
      - geo_score:      float 0-100
      - sentiment_raw:  float 0-100 (component)
      - time_weight:    float 0-1 (decaimento temporal)
      - final_weight:   float 0-1 (source_weight × time_weight)
    """
    direction    = article.get("direction",        "NEUTRAL")
    impact_score = float(article.get("impact_score",    0))
    relevance    = float(article.get("crypto_relevance", 50))
    source_w     = float(article.get("source_weight",   0.70))
    time_horizon = article.get("time_horizon",     "MID")
    published    = article.get("published",        "")
    macro_cat    = article.get("macro_category",   "MARKET")

    # ── 1. Sentimento (0-100) ─────────────────────────────────────────────
    sentiment_raw = _direction_to_sentiment(direction, impact_score)

    # ── 2. Impacto normalizado (0-100) ───────────────────────────────────
    impact_norm = min(100.0, max(0.0, impact_score))

    # ── 3. Relevância para crypto (já 0-100) ─────────────────────────────
    relevance_norm = min(100.0, max(0.0, relevance))

    # ── 4. Macro weight — categorias monetárias têm maior peso ────────────
    MACRO_IMPORTANCE = {
        "MONETARY":    100.0,
        "GEOPOLITICAL": 85.0,
        "REGULATION":   75.0,
        "MARKET":       60.0,
    }
    macro_weight_score = MACRO_IMPORTANCE.get(macro_cat, 60.0)

    # ── 5. Event frequency proxy — usa bull_hits + bear_hits como proxy ───
    bull_hits = int(article.get("bull_hits", 0))
    bear_hits = int(article.get("bear_hits", 0))
    total_hits = bull_hits + bear_hits
    # Mais triggers detectadas = mais relevante o evento
    event_freq_score = min(100.0, total_hits * 15.0)  # 7+ triggers = 100

    # ── Fórmula ponderada ─────────────────────────────────────────────────
    geo_score = (
        sentiment_raw   * GEO_WEIGHTS["sentiment"]    +
        impact_norm     * GEO_WEIGHTS["impact"]       +
        relevance_norm  * GEO_WEIGHTS["relevance"]    +
        macro_weight_score * GEO_WEIGHTS["macro_weight"] +
        event_freq_score * GEO_WEIGHTS["event_freq"]
    )
    geo_score = max(0.0, min(100.0, geo_score))

    # ── Peso final para agregação ─────────────────────────────────────────
    time_w    = _time_decay(published, time_horizon)
    final_w   = source_w * time_w

    return {
        **article,
        "geo_score":     round(geo_score, 1),
        "sentiment_raw": round(sentiment_raw, 1),
        "time_weight":   round(time_w, 3),
        "final_weight":  round(final_w, 3),
        # Componentes para debug
        "_components": {
            "sentiment":    round(sentiment_raw, 1),
            "impact":       round(impact_norm, 1),
            "relevance":    round(relevance_norm, 1),
            "macro_weight": round(macro_weight_score, 1),
            "event_freq":   round(event_freq_score, 1),
        },
    }


# ─── Agregação de score de mercado ─────────────────────────────────────────

def aggregate_market_sentiment(scored_articles: list) -> dict:
    """
    Agrega scores individuais em um score de mercado único (0-100).

    Usa média ponderada por `final_weight` + boost para eventos de alto impacto.

    Returns:
        {
            "market_score":    float 0-100,
            "confidence":      float 0-1,
            "dominant_dir":    "BULLISH" | "BEARISH" | "NEUTRAL",
            "article_count":   int,
            "high_impact_count": int,
            "weighted_avg":    float,
        }
    """
    if not scored_articles:
        return {
            "market_score":      50.0,
            "confidence":        0.0,
            "dominant_dir":      "NEUTRAL",
            "article_count":     0,
            "high_impact_count": 0,
            "weighted_avg":      50.0,
        }

    # ── Média ponderada ───────────────────────────────────────────────────
    total_w    = sum(a.get("final_weight", 0.5) for a in scored_articles)
    if total_w <= 0:
        total_w = len(scored_articles)

    weighted_sum = sum(
        a.get("geo_score", 50.0) * a.get("final_weight", 0.5)
        for a in scored_articles
    )
    weighted_avg = weighted_sum / total_w

    # ── Boost por evento de alto impacto ──────────────────────────────────
    high_impact = [
        a for a in scored_articles
        if float(a.get("impact_score", 0)) >= 85
           and a.get("direction", "NEUTRAL") != "NEUTRAL"
    ]

    boost = 0.0
    if high_impact:
        # Pega o evento de maior impacto
        top = max(high_impact, key=lambda x: float(x.get("impact_score", 0)))
        direction = top.get("direction", "NEUTRAL")
        if direction == "BULLISH":
            boost = +HIGH_IMPACT_BOOST
        elif direction == "BEARISH":
            boost = -HIGH_IMPACT_BOOST

    # Aplica boost (clamp 0-100)
    market_score = max(0.0, min(100.0, weighted_avg + boost))

    # ── Direção dominante ─────────────────────────────────────────────────
    if market_score >= 62:
        dominant_dir = "BULLISH"
    elif market_score <= 38:
        dominant_dir = "BEARISH"
    else:
        dominant_dir = "NEUTRAL"

    # ── Confiança: proporcional ao nº de artigos ──────────────────────────
    confidence = min(1.0, len(scored_articles) / (MIN_ARTICLES_CONFIDENCE * 3))

    return {
        "market_score":      round(market_score, 1),
        "confidence":        round(confidence, 2),
        "dominant_dir":      dominant_dir,
        "article_count":     len(scored_articles),
        "high_impact_count": len(high_impact),
        "weighted_avg":      round(weighted_avg, 1),
    }


# ─── Pipeline completo ────────────────────────────────────────────────────────

def run_sentiment_analysis(classified_articles: list) -> dict:
    """
    Pipeline completo: classifica → pontua → agrega.

    Args:
        classified_articles: lista de artigos já classificados pelo macro_classifier

    Returns:
        dict com market_score, confidence, dominant_dir, top_articles (3 mais relevantes)
    """
    if not classified_articles:
        return {
            "market_score":      50.0,
            "confidence":        0.0,
            "dominant_dir":      "NEUTRAL",
            "article_count":     0,
            "high_impact_count": 0,
            "weighted_avg":      50.0,
            "top_articles":      [],
        }

    # Pontua todos os artigos
    scored = [score_article(a) for a in classified_articles]

    # Ordena por geo_score × final_weight (mais relevante primeiro)
    scored.sort(
        key=lambda a: a.get("geo_score", 50.0) * a.get("final_weight", 0.5),
        reverse=True,
    )

    # Agrega o score de mercado
    agg = aggregate_market_sentiment(scored)

    # Top 3 artigos mais relevantes (para exibição)
    top_articles = [
        {
            "title":       a.get("title", ""),
            "source":      a.get("source", ""),
            "direction":   a.get("direction", "NEUTRAL"),
            "impact_score": a.get("impact_score", 0),
            "geo_score":   a.get("geo_score", 50.0),
            "macro_category": a.get("macro_category", "MARKET"),
            "time_horizon": a.get("time_horizon", "MID"),
            "detected_trigger": a.get("detected_trigger", ""),
        }
        for a in scored[:3]
    ]

    return {
        **agg,
        "top_articles": top_articles,
    }
