"""
news_fetcher.py — Async multi-source news fetcher
Trinity Trading | Geopolitical Engine

Fontes (21 RSS + 1 API):
  Macro Global:      Reuters, Federal Reserve, BIS, IMF, ZeroHedge, CNBC Economy
  Crypto Market:     CoinDesk, CoinTelegraph, The Block, Decrypt, Messari, Glassnode Insights
  Liquidez Global:   FRED Blog, ECB, Bank of Japan, US Treasury
  Crypto Fast News:  Bitcoin Magazine, Blockworks, Wu Blockchain
  Macro Economy:     Investing.com, MarketWatch
  API:               CryptoPanic (requer CRYPTOPANIC_API_KEY)

Notas:
  - Bloomberg, FT, WSJ: sem RSS gratuito (paywalled) — substituídos por CNBC/Reuters
  - Whale Alert: sem RSS gratuito — cobre via CryptoPanic
  - CryptoQuant: sem RSS gratuito — dados on-chain via onchain_model.py

Cache TTL: 15 minutos por fonte.
Fetching: paralelo via ThreadPoolExecutor.
"""

import time
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests
import feedparser

log = logging.getLogger(__name__)

# ─── Configurações ───────────────────────────────────────────────────────────

REQUEST_TIMEOUT  = 12    # segundos por fonte
CACHE_TTL        = 900   # 15 minutos
MAX_ARTICLES_SRC = 15    # artigos por fonte
MAX_WORKERS      = 14    # threads paralelas (aumentado para 21 fontes)

RSS_SOURCES = {

    # ── Macro Global ─────────────────────────────────────────────────────────
    "reuters": {
        "url":    "https://feeds.reuters.com/reuters/businessNews",
        "weight": 0.95,
        "category_hint": "MONETARY",
    },
    "federal_reserve": {
        "url":    "https://www.federalreserve.gov/feeds/press_all.xml",
        "weight": 1.00,  # credibilidade máxima
        "category_hint": "MONETARY",
    },
    "bis": {
        "url":    "https://www.bis.org/rss/speeches.rss",
        "weight": 0.95,
        "category_hint": "MONETARY",
    },
    "imf": {
        "url":    "https://www.imf.org/en/News/rss?language=eng",
        "weight": 0.95,
        "category_hint": "MONETARY",
    },
    "zerohedge": {
        "url":    "https://zerohedge.com/fullrss2.xml",
        "weight": 0.75,
        "category_hint": "MONETARY",
    },
    "cnbc_economy": {
        "url":    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
        "weight": 0.85,
        "category_hint": "MONETARY",
    },

    # ── Crypto Market ─────────────────────────────────────────────────────────
    "coindesk": {
        "url":    "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "weight": 0.90,
        "category_hint": "MARKET",
    },
    "cointelegraph": {
        "url":    "https://cointelegraph.com/rss",
        "weight": 0.85,
        "category_hint": "MARKET",
    },
    "theblock": {
        "url":    "https://www.theblock.co/rss.xml",
        "weight": 0.85,
        "category_hint": "MARKET",
    },
    "decrypt": {
        "url":    "https://decrypt.co/feed",
        "weight": 0.80,
        "category_hint": "MARKET",
    },
    "messari": {
        "url":    "https://messari.io/rss",
        "weight": 0.85,
        "category_hint": "MARKET",
    },
    "glassnode_insights": {
        "url":    "https://insights.glassnode.com/rss/",
        "weight": 0.90,
        "category_hint": "MARKET",
    },

    # ── Liquidez Global ───────────────────────────────────────────────────────
    "fred_blog": {
        "url":    "https://fredblog.stlouisfed.org/feed/",
        "weight": 0.90,
        "category_hint": "MONETARY",
    },
    "ecb": {
        "url":    "https://www.ecb.europa.eu/rss/press.html",
        "weight": 0.95,
        "category_hint": "MONETARY",
    },
    "boj": {
        "url":    "https://www.boj.or.jp/en/rss/siten.xml",
        "weight": 0.90,
        "category_hint": "MONETARY",
    },
    "us_treasury": {
        "url":    "https://home.treasury.gov/news/press-releases/rss.xml",
        "weight": 0.95,
        "category_hint": "MONETARY",
    },

    # ── Crypto Fast News ──────────────────────────────────────────────────────
    "bitcoin_magazine": {
        "url":    "https://bitcoinmagazine.com/feed",
        "weight": 0.80,
        "category_hint": "MARKET",
    },
    "blockworks": {
        "url":    "https://blockworks.co/feed",
        "weight": 0.80,
        "category_hint": "MARKET",
    },
    "wu_blockchain": {
        "url":    "https://wublockchain.substack.com/feed",
        "weight": 0.80,
        "category_hint": "MARKET",
    },

    # ── Macro Economy ─────────────────────────────────────────────────────────
    "investing_economy": {
        "url":    "https://www.investing.com/rss/news_14.rss",
        "weight": 0.80,
        "category_hint": "MONETARY",
    },
    "marketwatch": {
        "url":    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "weight": 0.85,
        "category_hint": "MARKET",
    },
}

CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"


# ─── Cache simples com TTL ────────────────────────────────────────────────────

_cache: dict = {}
_cache_ts: dict = {}


def _cache_get(key: str):
    if key in _cache and (time.time() - _cache_ts.get(key, 0)) < CACHE_TTL:
        return _cache[key]
    return None


def _cache_set(key: str, value):
    _cache[key] = value
    _cache_ts[key] = time.time()


# ─── Normalização de artigo ───────────────────────────────────────────────────

def _normalize_entry(entry: dict, source: str, source_weight: float,
                     category_hint: str) -> dict:
    """Converte entrada RSS bruta em formato normalizado."""
    title   = entry.get("title", "").strip()
    summary = entry.get("summary", entry.get("description", "")).strip()
    link    = entry.get("link", "")

    # Data de publicação
    pub = entry.get("published_parsed") or entry.get("updated_parsed")
    if pub:
        published = datetime(*pub[:6], tzinfo=timezone.utc).isoformat()
    else:
        published = datetime.now(timezone.utc).isoformat()

    return {
        "title":          title,
        "body":           summary[:500] if summary else title,
        "source":         source,
        "source_weight":  source_weight,
        "published":      published,
        "url":            link,
        "category_hint":  category_hint,
    }


# ─── Fetchers individuais ─────────────────────────────────────────────────────

def _fetch_rss(source_name: str, config: dict) -> list:
    """Busca e parseia um feed RSS."""
    cached = _cache_get(f"rss_{source_name}")
    if cached is not None:
        return cached

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TrinityTrading/1.0)",
            "Accept":     "application/rss+xml, application/xml, text/xml",
        }
        resp = requests.get(config["url"], headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)

        articles = []
        for entry in feed.entries[:MAX_ARTICLES_SRC]:
            art = _normalize_entry(
                dict(entry),
                source_name,
                config["weight"],
                config["category_hint"],
            )
            if art["title"]:
                articles.append(art)

        _cache_set(f"rss_{source_name}", articles)
        log.debug(f"   RSS {source_name}: {len(articles)} artigos")
        return articles

    except Exception as e:
        log.debug(f"   RSS {source_name} falhou: {type(e).__name__}: {e}")
        return []


def _fetch_cryptopanic() -> list:
    """Busca artigos da API CryptoPanic."""
    api_key = os.getenv("CRYPTOPANIC_API_KEY", "")
    if not api_key:
        return []

    cached = _cache_get("cryptopanic")
    if cached is not None:
        return cached

    try:
        params = {
            "auth_token": api_key,
            "public":     "true",
            "kind":       "news",
            "filter":     "important",
        }
        resp = requests.get(CRYPTOPANIC_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for item in data.get("results", [])[:MAX_ARTICLES_SRC]:
            articles.append({
                "title":         item.get("title", ""),
                "body":          item.get("title", ""),  # CryptoPanic não expõe corpo
                "source":        f"cryptopanic_{item.get('source', {}).get('title', 'CP')}",
                "source_weight": 0.85,
                "published":     item.get("published_at", datetime.now(timezone.utc).isoformat()),
                "url":           item.get("url", ""),
                "category_hint": "MARKET",
                "votes":         item.get("votes", {}),
            })

        _cache_set("cryptopanic", articles)
        log.debug(f"   CryptoPanic: {len(articles)} artigos")
        return articles

    except Exception as e:
        log.debug(f"   CryptoPanic falhou: {e}")
        return []


# ─── Agregador principal ──────────────────────────────────────────────────────

def fetch_all_news(max_age_minutes: int = 240) -> list:
    """
    Busca artigos de todas as fontes em paralelo.

    Args:
        max_age_minutes: filtra artigos mais velhos que N minutos

    Returns:
        Lista de artigos normalizados, ordenados por data (mais recente primeiro)
    """
    all_articles: list = []

    # Fetching paralelo
    tasks = {
        name: cfg for name, cfg in RSS_SOURCES.items()
    }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_rss, name, cfg): name
            for name, cfg in tasks.items()
        }
        futures[pool.submit(_fetch_cryptopanic)] = "cryptopanic"

        for future in as_completed(futures, timeout=REQUEST_TIMEOUT + 3):
            try:
                articles = future.result()
                all_articles.extend(articles)
            except Exception as e:
                log.debug(f"   Fetcher falhou: {e}")

    # Filtra por idade (remove artigos muito velhos)
    cutoff_ts = time.time() - max_age_minutes * 60
    fresh = []
    for art in all_articles:
        try:
            pub_dt = datetime.fromisoformat(art["published"].replace("Z", "+00:00"))
            if pub_dt.timestamp() >= cutoff_ts:
                fresh.append(art)
        except Exception:
            fresh.append(art)  # mantém se não conseguir parsear a data

    # Ordena por data (mais recente primeiro)
    try:
        fresh.sort(key=lambda a: a.get("published", ""), reverse=True)
    except Exception:
        pass

    log.info(f"   News fetcher: {len(fresh)} artigos frescos de {len(all_articles)} totais "
             f"({len(RSS_SOURCES)} RSS + CryptoPanic)")
    return fresh
