"""
geopolitical_intelligence_engine.py — Orquestrador Principal
Trinity Trading | Geopolitical Engine v1.0

Pipeline:
  1. Fetch news (RSS + CryptoPanic) via news_fetcher
  2. Classify articles via macro_classifier
  3. Score sentiment via sentiment_engine
  4. Calculate geo_score + risk/liquidity via macro_scoring
  5. Detect Rare Macro Setup
  6. Assemble final output dict

Output geopolítico para Trinity Score v2:
  geo_score         0-100
  geo_bias          BULLISH | BEARISH | NEUTRAL
  geo_confidence    0-100
  risk_sentiment    RISK_ON | RISK_OFF | NEUTRAL
  liquidity_outlook INCREASING | DECREASING | NEUTRAL
  top_event         (título, fonte, categoria)
  rare_macro_setup  bool
  top_articles      lista (3 mais relevantes)
"""

import logging
import time
from typing import Optional

from .news_fetcher    import fetch_all_news
from .macro_classifier import classify_all, get_top_event
from .sentiment_engine import run_sentiment_analysis
from .macro_scoring    import run_macro_scoring

log = logging.getLogger(__name__)

# ─── Cache de resultado (TTL 15 min) ─────────────────────────────────────────

_geo_cache:    Optional[dict] = None
_geo_cache_ts: float          = 0.0
GEO_CACHE_TTL = 900  # 15 minutos


# ─── Rare Macro Setup ─────────────────────────────────────────────────────────

# Combinações de triggers que caracterizam um Rare Macro Setup
RARE_MACRO_COMBOS = [
    # Expansão monetária + instabilidade bancária + crise de moeda
    {"quantitative easing", "banking crisis", "currency crisis"},
    {"rate cut", "bank failure", "currency devaluation"},
    # ETF + reserva soberana + adoção institucional
    {"etf approved", "bitcoin reserve", "institutional adoption"},
    {"spot etf", "sovereign bitcoin", "national reserve"},
    # Aperto severo + regulação + crashs
    {"rate hike", "ban crypto", "exchange collapse"},
    {"hawkish", "regulatory crackdown", "market crash"},
    # Geopolítico de alta intensidade + fuga para BTC
    {"banking crisis", "currency crisis", "de-dollarization"},
    {"sovereign default", "bank run", "dollar weakness"},
]

# Threshold: score de mercado muito divergente do neutro
RARE_MACRO_SCORE_THRESHOLD = 75.0  # abs(market_score - 50) >= 25


def _detect_rare_macro_setup(
    classified_articles: list,
    market_score: float,
) -> dict:
    """
    Detecta Rare Macro Setup: combinação de 3+ triggers de alto impacto.

    Returns:
        {"active": bool, "combo_matched": str | None, "score_divergence": float}
    """
    # Coleta triggers detectados nos artigos de alto impacto
    detected_triggers = set()
    for art in classified_articles:
        if float(art.get("impact_score", 0)) >= 75:
            t = art.get("detected_trigger", "")
            if t:
                detected_triggers.add(t)

    # Verifica combos
    matched_combo = None
    for combo in RARE_MACRO_COMBOS:
        if len(combo & detected_triggers) >= 2:  # 2+ triggers do combo = ativo
            matched_combo = " + ".join(sorted(combo & detected_triggers))
            break

    # Divergência do score em relação ao neutro (50)
    divergence = abs(market_score - 50.0)

    # Setup raro = combo OU score muito extremo
    active = bool(matched_combo) or divergence >= (RARE_MACRO_SCORE_THRESHOLD - 50)

    return {
        "active":          active,
        "combo_matched":   matched_combo,
        "score_divergence": round(divergence, 1),
    }


# ─── Output formatado ─────────────────────────────────────────────────────────

def _build_output(
    sentiment_result: dict,
    macro_result:     dict,
    rare_macro:       dict,
    top_event_tuple:  tuple,
    article_count:    int,
) -> dict:
    """Monta o dict de saída padronizado do geopolitical engine."""

    geo_score   = macro_result.get("geo_score",         50.0)
    geo_dir     = macro_result.get("geo_direction",      "NEUTRAL")
    risk        = macro_result.get("risk_sentiment",     "NEUTRAL")
    liquidity   = macro_result.get("liquidity_outlook",  "NEUTRAL")
    confidence  = sentiment_result.get("confidence",     0.0)

    # Converte confidence 0-1 para 0-100
    geo_confidence = round(confidence * 100, 1)

    # Top event
    top_title, top_source, top_cat = top_event_tuple

    return {
        # ── Scoring principal ──────────────────────────────────────────────
        "geo_score":          geo_score,
        "geo_bias":           geo_dir,
        "geo_confidence":     geo_confidence,

        # ── Modelos macro ──────────────────────────────────────────────────
        "risk_sentiment":     risk,
        "liquidity_outlook":  liquidity,

        # ── Evento principal ───────────────────────────────────────────────
        "top_event":          top_title,
        "top_event_source":   top_source,
        "top_event_category": top_cat,

        # ── Setup raro macro ───────────────────────────────────────────────
        "rare_macro_setup":   rare_macro.get("active",         False),
        "rare_macro_combo":   rare_macro.get("combo_matched",  None),

        # ── Detalhes de sentimento ─────────────────────────────────────────
        "market_score":       sentiment_result.get("market_score",      50.0),
        "high_impact_count":  sentiment_result.get("high_impact_count", 0),
        "article_count":      article_count,
        "top_articles":       sentiment_result.get("top_articles",      []),
    }


# ─── Engine principal ─────────────────────────────────────────────────────────

class GeoIntelligenceEngine:
    """
    Motor de inteligência geopolítica para o Trinity Trading.

    Uso:
        engine = GeoIntelligenceEngine()
        result = engine.run()
    """

    def __init__(self, max_age_minutes: int = 240):
        self.max_age_minutes = max_age_minutes

    def run(self, force: bool = False) -> dict:
        """
        Executa o pipeline geopolítico completo.

        Args:
            force: ignora cache e força re-fetch

        Returns:
            dict com geo_score, geo_bias, risk_sentiment, liquidity_outlook, etc.
        """
        global _geo_cache, _geo_cache_ts

        # Cache hit
        if not force and _geo_cache is not None:
            if (time.time() - _geo_cache_ts) < GEO_CACHE_TTL:
                log.debug("   Geo engine: cache hit")
                return _geo_cache

        log.info("   Geo engine: iniciando análise...")
        t0 = time.time()

        try:
            # ── 1. Fetch ───────────────────────────────────────────────────
            raw_articles = fetch_all_news(max_age_minutes=self.max_age_minutes)
            log.info(f"   Geo engine: {len(raw_articles)} artigos buscados")

            if not raw_articles:
                result = _neutral_output("sem artigos")
                _geo_cache    = result
                _geo_cache_ts = time.time()
                return result

            # ── 2. Classify ────────────────────────────────────────────────
            classified = classify_all(raw_articles)

            # ── 3. Sentiment ───────────────────────────────────────────────
            sentiment_result = run_sentiment_analysis(classified)

            market_score = sentiment_result.get("market_score", 50.0)
            confidence   = sentiment_result.get("confidence",   0.0)

            # ── 4. Macro scoring ───────────────────────────────────────────
            macro_result = run_macro_scoring(market_score, confidence, classified)

            # ── 5. Rare macro setup ────────────────────────────────────────
            rare_macro = _detect_rare_macro_setup(classified, market_score)

            # ── 6. Top event ───────────────────────────────────────────────
            top_event = get_top_event(classified)

            # ── 7. Assemble ────────────────────────────────────────────────
            result = _build_output(
                sentiment_result = sentiment_result,
                macro_result     = macro_result,
                rare_macro       = rare_macro,
                top_event_tuple  = top_event,
                article_count    = len(classified),
            )

            elapsed = time.time() - t0
            log.info(
                f"   Geo engine: geo_score={result['geo_score']:.1f} "
                f"bias={result['geo_bias']} risk={result['risk_sentiment']} "
                f"liq={result['liquidity_outlook']} "
                f"rare={result['rare_macro_setup']} "
                f"({elapsed:.1f}s)"
            )

            _geo_cache    = result
            _geo_cache_ts = time.time()
            return result

        except Exception as e:
            log.warning(f"   Geo engine falhou: {type(e).__name__}: {e}")
            return _neutral_output(f"erro: {e}")


def _neutral_output(reason: str = "") -> dict:
    """Retorna output neutro quando não há dados."""
    return {
        "geo_score":          50.0,
        "geo_bias":           "NEUTRAL",
        "geo_confidence":     0.0,
        "risk_sentiment":     "NEUTRAL",
        "liquidity_outlook":  "NEUTRAL",
        "top_event":          reason or "Sem dados geopolíticos",
        "top_event_source":   "N/A",
        "top_event_category": "MARKET",
        "rare_macro_setup":   False,
        "rare_macro_combo":   None,
        "market_score":       50.0,
        "high_impact_count":  0,
        "article_count":      0,
        "top_articles":       [],
    }


# ─── Função de conveniência ───────────────────────────────────────────────────

_engine_instance: Optional[GeoIntelligenceEngine] = None


def run_geo_analysis(force: bool = False) -> dict:
    """
    Entry point para o geopolitical engine (singleton).

    Uso direto:
        from geopolitical_engine import run_geo_analysis
        geo = run_geo_analysis()
    """
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = GeoIntelligenceEngine()
    return _engine_instance.run(force=force)
