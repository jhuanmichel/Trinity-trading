"""
macro_classifier.py — Classificador de Eventos Macro / Geopolíticos
Trinity Trading | Geopolitical Engine

Classifica artigos por:
  - Macro category: MONETARY | GEOPOLITICAL | REGULATION | MARKET
  - Crypto impact direction: BULLISH | BEARISH | NEUTRAL
  - Relevância para crypto: 0-100
  - Time horizon: SHORT (0-24h) | MID (1-7d) | LONG (7d+)
  - Detecção de eventos de alto impacto
"""

import re
from typing import Tuple

# ─── Clusters de palavras-chave ───────────────────────────────────────────────

# Tupla: (peso_impacto 0-100, macro_category, time_horizon)
BULLISH_TRIGGERS = {
    # === POLÍTICA MONETÁRIA EXPANSIONISTA === (muito bullish para crypto)
    "rate cut":                   (95, "MONETARY", "SHORT"),
    "rate cuts":                  (95, "MONETARY", "SHORT"),
    "cuts rates":                 (95, "MONETARY", "SHORT"),
    "cut interest":               (90, "MONETARY", "SHORT"),
    "lower interest":             (85, "MONETARY", "SHORT"),
    "fed pivot":                  (90, "MONETARY", "MID"),
    "policy pivot":               (85, "MONETARY", "MID"),
    "dovish":                     (80, "MONETARY", "MID"),
    "accommodative":              (75, "MONETARY", "MID"),
    "quantitative easing":        (95, "MONETARY", "MID"),
    " qe ":                       (90, "MONETARY", "MID"),
    "money printing":             (95, "MONETARY", "MID"),
    "stimulus":                   (80, "MONETARY", "MID"),
    "fiscal stimulus":            (80, "MONETARY", "MID"),
    "bailout":                    (75, "MONETARY", "SHORT"),
    "liquidity injection":        (90, "MONETARY", "SHORT"),
    "balance sheet expan":        (85, "MONETARY", "MID"),
    "emergency facility":         (85, "MONETARY", "SHORT"),
    "bank rescue":                (80, "MONETARY", "SHORT"),
    "credit easing":              (80, "MONETARY", "MID"),
    "pause rate":                 (70, "MONETARY", "SHORT"),

    # === INSTABILIDADE BANCÁRIA === (fuga para BTC)
    "bank failure":               (85, "GEOPOLITICAL", "SHORT"),
    "bank run":                   (85, "GEOPOLITICAL", "SHORT"),
    "banking crisis":             (90, "GEOPOLITICAL", "SHORT"),
    "bank collapse":              (90, "GEOPOLITICAL", "SHORT"),
    "systemic risk":              (80, "GEOPOLITICAL", "SHORT"),
    "deposit freeze":             (85, "GEOPOLITICAL", "SHORT"),
    "fdic":                       (75, "MONETARY", "SHORT"),
    "bank bailout":               (80, "MONETARY", "SHORT"),
    "credit contagion":           (80, "GEOPOLITICAL", "SHORT"),
    "debt crisis":                (80, "GEOPOLITICAL", "MID"),
    "sovereign default":          (85, "GEOPOLITICAL", "MID"),

    # === INFLAÇÃO / DESVALORIZAÇÃO MONETÁRIA ===
    "inflation surge":            (80, "MONETARY", "MID"),
    "inflation spike":            (80, "MONETARY", "MID"),
    "hyperinflation":             (95, "MONETARY", "MID"),
    "currency crisis":            (90, "GEOPOLITICAL", "MID"),
    "dollar weakness":            (80, "MONETARY", "MID"),
    "dollar decline":             (75, "MONETARY", "MID"),
    "dollar falls":               (70, "MONETARY", "SHORT"),
    "debasement":                 (85, "MONETARY", "LONG"),
    "currency devaluation":       (85, "GEOPOLITICAL", "MID"),
    "negative real rate":         (80, "MONETARY", "MID"),
    "purchasing power":           (70, "MONETARY", "LONG"),

    # === ADOÇÃO INSTITUCIONAL / CRIPTO ===
    "bitcoin reserve":            (95, "MARKET", "LONG"),
    "strategic bitcoin":          (95, "MARKET", "LONG"),
    "bitcoin etf":                (90, "REGULATION", "MID"),
    "spot etf":                   (90, "REGULATION", "MID"),
    "etf approved":               (95, "REGULATION", "SHORT"),
    "etf approval":               (90, "REGULATION", "SHORT"),
    "institutional adoption":     (85, "MARKET", "MID"),
    "bitcoin legal tender":       (95, "REGULATION", "LONG"),
    "sovereign bitcoin":          (95, "REGULATION", "LONG"),
    "national reserve":           (90, "REGULATION", "LONG"),
    "blackrock bitcoin":          (85, "MARKET", "MID"),
    "microstrategy":              (75, "MARKET", "SHORT"),
    "pension fund bitcoin":       (85, "MARKET", "LONG"),
    "sovereign wealth fund":      (80, "MARKET", "LONG"),
    "bitcoin accumulation":       (75, "MARKET", "MID"),
    "all-time high":              (65, "MARKET", "SHORT"),

    # === DE-DOLARIZAÇÃO ===
    "de-dollarization":           (80, "GEOPOLITICAL", "LONG"),
    "brics currency":             (75, "GEOPOLITICAL", "LONG"),
    "dollar alternative":         (75, "GEOPOLITICAL", "LONG"),
    "petrodollar end":            (80, "GEOPOLITICAL", "LONG"),
    "dollar hegemony":            (70, "GEOPOLITICAL", "LONG"),
    "sanctions bypass":           (75, "GEOPOLITICAL", "MID"),
}

BEARISH_TRIGGERS = {
    # === APERTO MONETÁRIO ===
    "rate hike":                  (95, "MONETARY", "SHORT"),
    "rate hikes":                 (95, "MONETARY", "SHORT"),
    "raises rates":               (90, "MONETARY", "SHORT"),
    "increased rates":            (90, "MONETARY", "SHORT"),
    "hawkish":                    (85, "MONETARY", "MID"),
    "quantitative tightening":    (90, "MONETARY", "MID"),
    " qt ":                       (85, "MONETARY", "MID"),
    "tightening monetary":        (85, "MONETARY", "MID"),
    "higher for longer":          (80, "MONETARY", "MID"),
    "no rate cut":                (80, "MONETARY", "SHORT"),
    "rates stay elevated":        (80, "MONETARY", "MID"),
    "restrictive policy":         (80, "MONETARY", "MID"),
    "balance sheet reduction":    (85, "MONETARY", "MID"),

    # === REGULAÇÃO CRIPTO / ENFORCEMENT ===
    "sec lawsuit":                (90, "REGULATION", "SHORT"),
    "sec charges":                (90, "REGULATION", "SHORT"),
    "sec sues":                   (90, "REGULATION", "SHORT"),
    "ban crypto":                 (95, "REGULATION", "MID"),
    "ban bitcoin":                (95, "REGULATION", "MID"),
    "crypto ban":                 (95, "REGULATION", "MID"),
    "regulatory crackdown":       (85, "REGULATION", "MID"),
    "exchange collapse":          (95, "MARKET", "SHORT"),
    "exchange bankrupt":          (90, "MARKET", "SHORT"),
    "exchange hack":              (85, "MARKET", "SHORT"),
    "billion stolen":             (85, "MARKET", "SHORT"),
    "fraud":                      (80, "REGULATION", "SHORT"),
    "ponzi":                      (85, "REGULATION", "SHORT"),
    "assets seized":              (80, "REGULATION", "SHORT"),
    "criminal charges":           (80, "REGULATION", "SHORT"),
    "money laundering":           (75, "REGULATION", "MID"),
    "compliance failure":         (70, "REGULATION", "MID"),

    # === FORÇA DO DÓLAR / AVERSÃO AO RISCO ===
    "dollar surge":               (75, "MONETARY", "SHORT"),
    "dxy surge":                  (75, "MONETARY", "SHORT"),
    "dollar strengthens":         (70, "MONETARY", "SHORT"),
    "flight to safety":           (75, "MONETARY", "SHORT"),
    "risk aversion":              (75, "MARKET", "SHORT"),
    "risk-off":                   (70, "MARKET", "SHORT"),
    "sell-off":                   (65, "MARKET", "SHORT"),
    "market crash":               (85, "MARKET", "SHORT"),
    "recession":                  (75, "MONETARY", "MID"),
    "stagflation":                (75, "MONETARY", "MID"),
    "credit crunch":              (80, "MONETARY", "MID"),
    "liquidity drain":            (80, "MONETARY", "MID"),

    # === RISCO GEOPOLÍTICO (bearish curto prazo) ===
    "nuclear threat":             (85, "GEOPOLITICAL", "SHORT"),
    "military strike":            (70, "GEOPOLITICAL", "SHORT"),
    "war escalation":             (75, "GEOPOLITICAL", "SHORT"),
    "conflict escalates":         (70, "GEOPOLITICAL", "SHORT"),
    "invasion":                   (70, "GEOPOLITICAL", "SHORT"),
    "missile attack":             (70, "GEOPOLITICAL", "SHORT"),
    "terror attack":              (75, "GEOPOLITICAL", "SHORT"),
    "cyber attack":               (65, "GEOPOLITICAL", "SHORT"),
    "trade war":                  (70, "GEOPOLITICAL", "MID"),
    "sanctions":                  (65, "GEOPOLITICAL", "MID"),
    "tariff":                     (60, "GEOPOLITICAL", "MID"),
}

# Palavras de negação — invertem o sinal de keywords próximas
NEGATION_WORDS = {"no", "not", "never", "neither", "nor", "without",
                  "halt", "stop", "end", "pause", "freeze"}

# Pesos de credibilidade por fonte
SOURCE_WEIGHTS = {
    "federal_reserve":    1.00,
    "reuters":            0.95,
    "bloomberg":          0.95,
    "marketwatch":        0.85,
    "coindesk":           0.90,
    "theblock":           0.85,
    "cointelegraph":      0.80,
    "decrypt":            0.75,
    "cryptopanic":        0.75,
    "default":            0.70,
}

# Categorias macro para agrupamento
CATEGORY_KEYWORDS = {
    "MONETARY":    ["fed", "fomc", "federal reserve", "interest rate", "inflation",
                    "gdp", "cpi", "ppi", "nfp", "quantitative", "taper", "pivot",
                    "powell", "yellen", "boj", "ecb", "monetary", "treasury",
                    "yield curve", "yield", "central bank", "debt ceiling"],
    "GEOPOLITICAL":["war", "conflict", "escalation", "sanction", "invasion", "coup",
                    "regime", "crisis", "nuclear", "missile", "nato", "ukraine",
                    "russia", "china", "taiwan", "middle east", "iran", "north korea",
                    "geopolitical", "military", "political instability", "bank run",
                    "bank failure", "sovereign", "default"],
    "REGULATION":  ["sec", "cftc", "regulation", "ban", "bill", "law", "approve",
                    "compliance", "enforcement", "lawsuit", "congress", "senate",
                    "legislation", "regulatory", "legal", "crypto law", "policy",
                    "etf", "approval", "mica", "eu crypto"],
    "MARKET":      ["etf", "hedge fund", "institution", "adoption", "accumulation",
                    "whale", "treasury", "balance sheet", "fund", "asset manager",
                    "portfolio", "stockpile", "purchase", "all-time high", "ath",
                    "exchange", "defi", "stablecoin", "mining"],
}


# ─── Funções de classificação ─────────────────────────────────────────────────

def _text(article: dict) -> str:
    """Extrai texto completo do artigo para análise."""
    return (article.get("title", "") + " " + article.get("body", "")).lower()


def _check_negation(text: str, pos: int, window: int = 6) -> bool:
    """Verifica se há palavra de negação nos N tokens antes da posição."""
    tokens = text[:pos].split()[-window:]
    return bool(NEGATION_WORDS.intersection(set(tokens)))


def classify_article(article: dict) -> dict:
    """
    Classifica um artigo com:
    - macro_category: MONETARY | GEOPOLITICAL | REGULATION | MARKET
    - direction: BULLISH | BEARISH | NEUTRAL
    - impact_score: 0-100 (peso do evento mais forte detectado)
    - crypto_relevance: 0-100
    - time_horizon: SHORT | MID | LONG
    - detected_trigger: keyword principal detectada
    """
    text = _text(article)

    # ── Detecta triggers bullish e bearish ────────────────────────────────
    bull_hits: list = []
    bear_hits: list = []

    for phrase, (weight, cat, horizon) in BULLISH_TRIGGERS.items():
        idx = text.find(phrase)
        if idx >= 0 and not _check_negation(text, idx):
            bull_hits.append((weight, cat, horizon, phrase))

    for phrase, (weight, cat, horizon) in BEARISH_TRIGGERS.items():
        idx = text.find(phrase)
        if idx >= 0 and not _check_negation(text, idx):
            bear_hits.append((weight, cat, horizon, phrase))

    # ── Score direcional ──────────────────────────────────────────────────
    bull_score = max((h[0] for h in bull_hits), default=0)
    bear_score = max((h[0] for h in bear_hits), default=0)

    if bull_score > bear_score + 10:
        direction    = "BULLISH"
        top_hit      = max(bull_hits, key=lambda x: x[0])
        impact_score = bull_score
    elif bear_score > bull_score + 10:
        direction    = "BEARISH"
        top_hit      = max(bear_hits, key=lambda x: x[0])
        impact_score = bear_score
    else:
        direction    = "NEUTRAL"
        top_hit      = max(bull_hits + bear_hits, key=lambda x: x[0]) \
                       if bull_hits or bear_hits else (0, "MARKET", "SHORT", "")
        impact_score = max(bull_score, bear_score)

    # ── Macro category ────────────────────────────────────────────────────
    cat_scores: dict[str, int] = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        cat_scores[cat] = sum(1 for kw in keywords if kw in text)

    # Prioridade: trigger detectada > contagem de keywords
    if top_hit[1] != "MARKET" or max(cat_scores.values(), default=0) == 0:
        macro_category = top_hit[1] if top_hit[0] > 0 else "MARKET"
    else:
        macro_category = max(cat_scores, key=cat_scores.get)

    # ── Crypto relevance ─────────────────────────────────────────────────
    CRYPTO_WORDS = ["bitcoin", "btc", "crypto", "ethereum", "eth", "defi",
                    "blockchain", "digital asset", "stablecoin", "coinbase",
                    "binance", "altcoin", "nft", "web3", "satoshi"]
    crypto_hits  = sum(1 for w in CRYPTO_WORDS if w in text)
    # Notícias macro genéricas sempre são relevantes (Fed, guerra, inflação)
    macro_hits   = sum(1 for kw in CATEGORY_KEYWORDS["MONETARY"] + CATEGORY_KEYWORDS["GEOPOLITICAL"]
                       if kw in text)
    crypto_relevance = min(100, (crypto_hits * 15) + (macro_hits * 8) + (impact_score * 0.3))

    # ── Time horizon ──────────────────────────────────────────────────────
    time_horizon = top_hit[2] if top_hit[0] > 0 else "MID"

    return {
        **article,
        "direction":       direction,
        "impact_score":    impact_score,
        "macro_category":  macro_category,
        "time_horizon":    time_horizon,
        "crypto_relevance":min(100, int(crypto_relevance)),
        "detected_trigger":top_hit[3],
        "bull_hits":       len(bull_hits),
        "bear_hits":       len(bear_hits),
        "source_weight":   SOURCE_WEIGHTS.get(article.get("source", "").split("_")[0], 0.70),
    }


def classify_all(articles: list) -> list:
    """Classifica todos os artigos."""
    return [classify_article(a) for a in articles]


def get_top_event(articles: list) -> Tuple[str, str, str]:
    """
    Retorna (título, source, categoria) do evento de maior impacto.
    """
    if not articles:
        return ("Sem dados geopolíticos", "N/A", "MARKET")

    relevant = [a for a in articles if a.get("impact_score", 0) >= 50]
    if not relevant:
        relevant = articles[:1]

    top = max(relevant, key=lambda a: (a.get("impact_score", 0) *
                                       a.get("source_weight", 0.7)))
    title = top.get("title", "N/A")
    if len(title) > 80:
        title = title[:77] + "..."
    return (title, top.get("source", "N/A"), top.get("macro_category", "MARKET"))
