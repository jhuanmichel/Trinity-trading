"""
news_fetcher.py — Coleta notícias de RSS e Twitter/X público via Nitter.

Tier 1 (poll 2 min): CoinDesk, CoinTelegraph, Bloomberg Crypto, The Block,
    Decrypt, Reuters Tech + Nitter: Trump, POTUS, SECGov, Fed, WhiteHouse
Tier 2 (poll 5 min): Bitcoin Magazine, CNBC Crypto, CryptoNews
    + Nitter: Musk, Saylor, CZ, Armstrong, Vitalik
Tier 3 (poll 15 min): WSJ World, Reuters Business, Politico

Deduplicação por hash do título — resetado a cada 24h.
Nunca lança exceção — cada fonte falha silenciosamente.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "crypto", "ethereum", "blockchain",
    "fed", "interest rate", "inflation", "tariff", "sanction",
    "trump", "sec", "regulation", "ban", "hack", "etf",
    "binance", "coinbase", "tether", "stablecoin",
]


class NewsFetcher:

    SOURCES: dict = {
        "tier1": [
            # RSS — impacto imediato
            {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/",       "name": "CoinDesk"},
            {"url": "https://cointelegraph.com/rss",                          "name": "CoinTelegraph"},
            {"url": "https://feeds.bloomberg.com/crypto/news.rss",            "name": "Bloomberg Crypto"},
            {"url": "https://theblock.co/rss.xml",                            "name": "The Block"},
            {"url": "https://decrypt.co/feed",                                "name": "Decrypt"},
            {"url": "https://www.reuters.com/technology/rss",                 "name": "Reuters Tech"},
            # Nitter (Twitter/X público sem API key)
            {"url": "https://nitter.poast.org/realDonaldTrump/rss",           "name": "Trump"},
            {"url": "https://nitter.poast.org/POTUS/rss",                     "name": "POTUS"},
            {"url": "https://nitter.poast.org/SECGov/rss",                    "name": "SEC"},
            {"url": "https://nitter.poast.org/federalreserve/rss",            "name": "Federal Reserve"},
            {"url": "https://nitter.poast.org/WhiteHouse/rss",                "name": "White House"},
        ],
        "tier2": [
            {"url": "https://bitcoinmagazine.com/feed",                       "name": "Bitcoin Magazine"},
            {"url": "https://www.cnbc.com/id/100727362/device/rss/rss.html",  "name": "CNBC Crypto"},
            {"url": "https://cryptonews.com/news/feed/",                      "name": "CryptoNews"},
            {"url": "https://nitter.poast.org/elonmusk/rss",                  "name": "Elon Musk"},
            {"url": "https://nitter.poast.org/michael_saylor/rss",            "name": "Saylor"},
            {"url": "https://nitter.poast.org/cz_binance/rss",                "name": "CZ Binance"},
            {"url": "https://nitter.poast.org/brian_armstrong/rss",           "name": "Brian Armstrong"},
            {"url": "https://nitter.poast.org/VitalikButerin/rss",            "name": "Vitalik"},
        ],
        "tier3": [
            {"url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",            "name": "WSJ World"},
            {"url": "https://feeds.reuters.com/reuters/businessNews",         "name": "Reuters Business"},
            {"url": "https://rss.politico.com/politics-news.xml",             "name": "Politico"},
        ],
    }

    POLL_INTERVALS: dict = {"tier1": 120, "tier2": 300, "tier3": 900}

    def __init__(self) -> None:
        self._seen_ids: set = set()
        self._seen_reset: datetime = datetime.now(timezone.utc)

    # ── Interface pública ─────────────────────────────────────────────────────

    def fetch_latest(self, tier: str) -> list:
        """
        Busca RSS de todas as fontes do tier.
        Retorna lista de notícias novas (não vistas) com campos:
          id, title, summary, source, tier, url, published, fetched_at
        Nunca lança exceção.
        """
        self._maybe_reset_seen()
        results: list = []

        for src in self.SOURCES.get(tier, []):
            try:
                items = self._parse_rss(src["url"], src["name"], tier)
                for item in items:
                    if item["id"] not in self._seen_ids:
                        self._seen_ids.add(item["id"])
                        if self._is_crypto_relevant(item["title"], item["summary"]):
                            results.append(item)
            except Exception as e:
                logger.debug(f"[NewsFetcher] {src['name']} falhou: {e}")

        if results:
            logger.info(f"[NewsFetcher] {tier}: {len(results)} novas notícias relevantes")
        return results

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse_rss(self, url: str, source_name: str, tier: str) -> list:
        """
        Parseia RSS via feedparser (já em requirements.txt).
        Usa requests para fetch com timeout, feedparser para parsing.
        Fallback direto para feedparser.parse(url) se requests falhar.
        """
        import feedparser
        import requests

        now_iso = datetime.now(timezone.utc).isoformat()
        items: list = []

        try:
            raw = None
            try:
                resp = requests.get(
                    url, timeout=5,
                    headers={"User-Agent": "Mozilla/5.0 TrinityBot/1.0"}
                )
                if resp.status_code == 200:
                    raw = resp.text
            except Exception as req_e:
                logger.debug(f"[NewsFetcher] requests falhou para {source_name}: {req_e}")

            feed = feedparser.parse(raw if raw else url)

            for entry in (feed.entries or [])[:20]:
                title   = (getattr(entry, "title",   "") or "").strip()
                summary = (getattr(entry, "summary", "")
                           or getattr(entry, "description", "") or "")[:500].strip()
                link    = getattr(entry, "link", "") or ""

                if not title:
                    continue

                # Timestamp de publicação
                pub_iso = now_iso
                try:
                    pub_str = getattr(entry, "published", "") or ""
                    if pub_str:
                        import email.utils
                        parsed_t = email.utils.parsedate_to_datetime(pub_str)
                        pub_iso  = parsed_t.astimezone(timezone.utc).isoformat()
                except Exception:
                    pass

                news_id = hashlib.md5(
                    title.encode("utf-8", errors="replace")
                ).hexdigest()[:16]

                items.append({
                    "id":         news_id,
                    "title":      title,
                    "summary":    summary,
                    "source":     source_name,
                    "tier":       tier,
                    "url":        link,
                    "published":  pub_iso,
                    "fetched_at": now_iso,
                })

        except Exception as e:
            logger.debug(f"[NewsFetcher] _parse_rss({source_name}): {e}")

        return items

    def _is_crypto_relevant(self, title: str, summary: str) -> bool:
        """
        Filtro rápido antes de classificar — evita gastar tokens da Claude API
        em notícias irrelevantes.
        Retorna True se o texto contém ao menos uma CRYPTO_KEYWORD.
        """
        text = (title + " " + summary).lower()
        return any(kw in text for kw in CRYPTO_KEYWORDS)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _maybe_reset_seen(self) -> None:
        """Reseta o set de IDs vistos a cada 24h."""
        now = datetime.now(timezone.utc)
        if (now - self._seen_reset).total_seconds() > 86400:
            self._seen_ids.clear()
            self._seen_reset = now
            logger.debug("[NewsFetcher] Set de IDs vistos resetado (24h)")
