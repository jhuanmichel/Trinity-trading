"""
news_classifier.py — Classifica notícias via Claude API (modelo haiku).

Uso de tokens mínimo: MAX_TOKENS=150, cache de 1h para evitar reclassificar.
Fallback NEUTRAL + lock_minutes=0 em qualquer falha de API.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """
Você é um analista de risco de mercado especializado em Bitcoin e criptomoedas.
Analise esta notícia e classifique seu impacto provável no preço do Bitcoin
nos próximos 60 minutos.

Notícia: {headline}
Fonte: {source}
Resumo: {summary}

Responda APENAS com JSON válido, sem texto adicional:
{{
  "impact":        "BULLISH" | "BEARISH" | "NEUTRAL",
  "magnitude":     "HIGH" | "MEDIUM" | "LOW",
  "confidence":    "HIGH" | "MEDIUM" | "LOW",
  "lock_minutes":  int,
  "score_adjust":  float,
  "reason":        "string curta, max 15 palavras"
}}

Regras para lock_minutes (minutos para travar novos sinais):
  HIGH   + BEARISH/BULLISH: 30
  MEDIUM + BEARISH/BULLISH: 15
  LOW    ou NEUTRAL:        0

Regras para score_adjust (multiplicador adicional no score dos sinais):
  HIGH   BEARISH:  -0.20
  MEDIUM BEARISH:  -0.10
  HIGH   BULLISH:  +0.15
  MEDIUM BULLISH:  +0.08
  NEUTRAL ou LOW:   0.00
"""


class NewsClassifier:

    MODEL      = "claude-haiku-4-5"
    MAX_TOKENS = 150
    CACHE_TTL  = 3600  # 1h

    def __init__(self) -> None:
        self._cache: dict = {}  # news_id → {"result": dict, "cached_at": float}

    # ── Interface pública ─────────────────────────────────────────────────────

    def classify(self, news_item: dict) -> dict:
        """
        Classifica notícia via Claude API.
        Cache por CACHE_TTL segundos para evitar reclassificar a mesma notícia.
        Fallback neutro em qualquer falha.
        Retorna dict com: impact, magnitude, confidence, lock_minutes,
        score_adjust, reason, news_id, title.
        """
        news_id = news_item.get("id", "")

        # Cache hit
        cached = self._cache.get(news_id)
        if cached and (time.time() - cached["cached_at"]) < self.CACHE_TTL:
            logger.debug(f"[NewsClassifier] Cache hit: {news_id}")
            return cached["result"]

        try:
            prompt = CLASSIFICATION_PROMPT.format(
                headline=news_item.get("title",   ""),
                source  =news_item.get("source",  ""),
                summary =news_item.get("summary", ""),
            )
            raw    = self._call_claude_api(prompt)
            result = {**raw, "news_id": news_id, "title": news_item.get("title", "")}
            logger.info(
                f"[NewsClassifier] {result['impact']} {result['magnitude']} "
                f"(conf={result['confidence']}) — {news_item.get('source')}: "
                f"{news_item.get('title', '')[:60]}"
            )
        except Exception as e:
            logger.warning(
                f"[NewsClassifier] Falha ao classificar "
                f"'{news_item.get('title', '')}': {e}"
            )
            result = self._safe_classify_fallback()
            result["news_id"] = news_id
            result["title"]   = news_item.get("title", "")

        self._cache[news_id] = {"result": result, "cached_at": time.time()}
        return result

    def _safe_classify_fallback(self) -> dict:
        """Classificação neutra segura em caso de falha total de API."""
        return {
            "impact":       "NEUTRAL",
            "magnitude":    "LOW",
            "confidence":   "LOW",
            "lock_minutes": 0,
            "score_adjust": 0.0,
            "reason":       "classification_failed",
        }

    # ── Chamada API ───────────────────────────────────────────────────────────

    def _call_claude_api(self, prompt: str) -> dict:
        """
        Chama Anthropic API com timeout de 8s.
        Usa ANTHROPIC_API_KEY do environment ou config.py.
        """
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            try:
                from config import ANTHROPIC_API_KEY as _key
                api_key = _key
            except Exception:
                pass

        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY não configurada")

        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model      = self.MODEL,
            max_tokens = self.MAX_TOKENS,
            messages   = [{"role": "user", "content": prompt}],
        )

        raw_text = (message.content[0].text or "").strip()

        # Remove possível markdown code fence
        if "```" in raw_text:
            parts    = raw_text.split("```")
            raw_text = parts[1] if len(parts) > 1 else raw_text
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()

        parsed = json.loads(raw_text)

        # Valida campos obrigatórios
        required = {"impact", "magnitude", "confidence", "lock_minutes", "score_adjust", "reason"}
        missing  = required - set(parsed.keys())
        if missing:
            raise ValueError(f"Campos ausentes na resposta: {missing}")

        # Normaliza e valida tipos
        parsed["lock_minutes"] = max(0, int(parsed.get("lock_minutes", 0)))
        parsed["score_adjust"] = float(parsed.get("score_adjust", 0.0))

        # Valida enums
        if parsed["impact"] not in ("BULLISH", "BEARISH", "NEUTRAL"):
            parsed["impact"] = "NEUTRAL"
        if parsed["magnitude"] not in ("HIGH", "MEDIUM", "LOW"):
            parsed["magnitude"] = "LOW"
        if parsed["confidence"] not in ("HIGH", "MEDIUM", "LOW"):
            parsed["confidence"] = "LOW"

        return parsed
