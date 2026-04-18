"""
news_sentinel.py — Orquestrador do News Sentiment Engine.

Integra NewsFetcher + NewsClassifier e expõe interface para o pipeline de sinais:
  - check_signal_clearance() → libera ou trava sinal Telegram
  - get_score_multiplier()   → multiplicador de score [0.80, 1.15] ou 0.0 se locked
  - get_macro_context_for_alert() → string de contexto para o Telegram

Estado persistido em: dashboard/news_state.json
Logs em:              logs/news_YYYY-MM-DD.jsonl

Filosofia: fail-open para notícias, fail-closed para estrutura técnica.
Se qualquer componente falhar → sinal passa normalmente (multiplier=1.0).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

NEWS_STATE_FILE    = Path("dashboard/news_state.json")
LOGS_DIR           = Path("logs")
MULTIPLIER_FLOOR   = 0.80
MULTIPLIER_CEILING = 1.15


def _adj_to_multiplier(score_adjust: float) -> float:
    """Converte score_adjust aditivo em multiplicador clampado [0.80, 1.15]."""
    raw = 1.0 + score_adjust
    return round(max(MULTIPLIER_FLOOR, min(MULTIPLIER_CEILING, raw)), 4)


# ── Singleton ─────────────────────────────────────────────────────────────────
_instance: Optional["NewsSentinel"] = None


def get_sentinel() -> "NewsSentinel":
    """Retorna instância singleton do NewsSentinel."""
    global _instance
    if _instance is None:
        _instance = NewsSentinel()
    return _instance


class NewsSentinel:

    def __init__(self) -> None:
        self._fetcher    = None
        self._classifier = None
        self._state      = self._load_state()
        self._last_tier2 = 0.0
        self._last_tier3 = 0.0

    def _lazy_init(self) -> None:
        """Inicializa fetcher e classifier na primeira chamada (lazy import)."""
        if self._fetcher is None:
            from news_fetcher    import NewsFetcher
            from news_classifier import NewsClassifier
            self._fetcher    = NewsFetcher()
            self._classifier = NewsClassifier()

    # ── Estado ────────────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        """Carrega ou inicializa dashboard/news_state.json."""
        default: dict = {
            "locked_until":        None,
            "lock_reason":         None,
            "current_sentiment":   "NEUTRAL",
            "sentiment_score_adj": 0.0,
            "last_high_impact":    None,
            "last_checked":        datetime.now(timezone.utc).isoformat(),
            "active_news":         [],
        }
        try:
            if NEWS_STATE_FILE.exists():
                data = json.loads(NEWS_STATE_FILE.read_text())
                for k, v in default.items():
                    data.setdefault(k, v)
                return data
        except Exception as e:
            logger.warning(f"[NewsSentinel] Erro ao carregar state: {e}")
        # Salva o default imediatamente para garantir que o arquivo existe
        try:
            NEWS_STATE_FILE.parent.mkdir(exist_ok=True)
            NEWS_STATE_FILE.write_text(json.dumps(default, indent=2, default=str))
        except Exception:
            pass
        return default

    def _save_state(self) -> None:
        """Persiste estado em news_state.json."""
        try:
            NEWS_STATE_FILE.parent.mkdir(exist_ok=True)
            NEWS_STATE_FILE.write_text(
                json.dumps(self._state, indent=2, default=str)
            )
        except Exception as e:
            logger.warning(f"[NewsSentinel] Erro ao salvar state: {e}")

    # ── Verificações ──────────────────────────────────────────────────────────

    def is_locked(self) -> bool:
        """True se o sistema está travado por evento macro (locked_until no futuro)."""
        locked_until = self._state.get("locked_until")
        if not locked_until:
            return False
        try:
            until_dt = datetime.fromisoformat(locked_until)
            # Garante timezone-aware
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < until_dt
        except Exception:
            return False

    def get_score_multiplier(self) -> float:
        """
        Multiplicador de score atual [0.80, 1.15].
        Retorna 0.0 se locked (sinal não deve passar de forma alguma).
        """
        if self.is_locked():
            return 0.0
        adj = self._state.get("sentiment_score_adj", 0.0)
        return _adj_to_multiplier(adj)

    # ── Ciclo principal ───────────────────────────────────────────────────────

    def run_cycle(self) -> None:
        """
        Executado a cada 2 min pelo loop assíncrono em server.py.
        1. Fetch por tier (respeitando POLL_INTERVALS)
        2. Classifica notícias novas via Claude API
        3. Processa classificações (lock, Telegram, log)
        4. Atualiza sentiment_score_adj com média ponderada
        5. Persiste estado
        """
        try:
            self._lazy_init()
            import time
            now = time.time()

            news_batch: list = []

            # Tier 1 — sempre (a cada 2 min via loop)
            try:
                news_batch.extend(self._fetcher.fetch_latest("tier1"))
            except Exception as e:
                logger.warning(f"[NewsSentinel] Tier1 fetch falhou: {e}")

            # Tier 2 — a cada 5 min
            if now - self._last_tier2 >= 300:
                try:
                    news_batch.extend(self._fetcher.fetch_latest("tier2"))
                    self._last_tier2 = now
                except Exception as e:
                    logger.warning(f"[NewsSentinel] Tier2 fetch falhou: {e}")

            # Tier 3 — a cada 15 min
            if now - self._last_tier3 >= 900:
                try:
                    news_batch.extend(self._fetcher.fetch_latest("tier3"))
                    self._last_tier3 = now
                except Exception as e:
                    logger.warning(f"[NewsSentinel] Tier3 fetch falhou: {e}")

            # Classifica notícias novas
            high_adjs:   list = []
            medium_adjs: list = []

            for news in news_batch:
                try:
                    cls = self._classifier.classify(news)
                    magnitude = cls.get("magnitude", "LOW")
                    impact    = cls.get("impact",    "NEUTRAL")
                    self._process_classification(news, cls)
                    if magnitude == "HIGH" and impact != "NEUTRAL":
                        high_adjs.append(cls.get("score_adjust", 0.0))
                    elif magnitude == "MEDIUM" and impact != "NEUTRAL":
                        medium_adjs.append(cls.get("score_adjust", 0.0))
                except Exception as e:
                    logger.debug(f"[NewsSentinel] Classificação falhou: {e}")

            # Atualiza sentiment_score_adj (HIGH pesa 2x)
            if high_adjs or medium_adjs:
                all_weighted = [a * 2 for a in high_adjs] + medium_adjs
                new_adj = sum(all_weighted) / len(all_weighted) if all_weighted else 0.0
                prev    = self._state.get("sentiment_score_adj", 0.0)
                self._state["sentiment_score_adj"] = round(prev * 0.4 + new_adj * 0.6, 4)
            elif not self.is_locked():
                # Decai gradualmente para neutro quando sem notícias recentes
                prev = self._state.get("sentiment_score_adj", 0.0)
                self._state["sentiment_score_adj"] = round(prev * 0.85, 4)
                if abs(self._state["sentiment_score_adj"]) < 0.01:
                    self._state["sentiment_score_adj"] = 0.0

            # Atualiza current_sentiment
            adj = self._state["sentiment_score_adj"]
            if adj >= 0.05:
                self._state["current_sentiment"] = "BULLISH"
            elif adj <= -0.05:
                self._state["current_sentiment"] = "BEARISH"
            else:
                self._state["current_sentiment"] = "NEUTRAL"

            self._state["last_checked"] = datetime.now(timezone.utc).isoformat()
            self._save_state()

        except Exception as e:
            logger.error(f"[NewsSentinel] Erro no run_cycle: {e}")

    def _process_classification(self, news: dict, cls: dict) -> None:
        """
        Processa resultado de classificação:
          HIGH impact  → lock + Telegram + log
          MEDIUM       → atualizar sentiment_score_adj + log
          LOW/NEUTRAL  → ignorar
        """
        magnitude = cls.get("magnitude", "LOW")
        impact    = cls.get("impact",    "NEUTRAL")
        title     = news.get("title",    "")

        # Adiciona à lista active_news (max 10)
        news_entry = {
            "title":      title,
            "source":     news.get("source", ""),
            "impact":     impact,
            "magnitude":  magnitude,
            "confidence": cls.get("confidence", "LOW"),
            "reason":     cls.get("reason",     ""),
            "timestamp":  news.get("fetched_at", datetime.now(timezone.utc).isoformat()),
        }
        active = self._state.get("active_news", [])
        active.insert(0, news_entry)
        self._state["active_news"] = active[:10]

        if magnitude == "HIGH" and impact != "NEUTRAL":
            lock_min = int(cls.get("lock_minutes", 30))
            until_dt = datetime.now(timezone.utc) + timedelta(minutes=lock_min)
            self._state["locked_until"]     = until_dt.isoformat()
            self._state["lock_reason"]      = f"{impact}: {cls.get('reason', '')}"
            self._state["last_high_impact"] = news_entry

            logger.warning(
                f"[NewsSentinel] 🔒 LOCK MACRO {impact} HIGH por {lock_min}min — "
                f"{news.get('source')}: {title[:80]}"
            )
            # DESATIVADO: alertas individuais removidos para economizar créditos Anthropic.
            # Resumo macro incluído no digest semanal (domingo 20:00 UTC).
            # self._send_telegram_alert(news, cls)
            logger.info(
                f"[NewsSentinel] Alerta Telegram DESATIVADO: {impact} HIGH — "
                f"{title[:60]}"
            )
            self._log_news(news, cls)

        elif magnitude == "MEDIUM" and impact != "NEUTRAL":
            logger.info(
                f"[NewsSentinel] Notícia MEDIUM {impact}: "
                f"{news.get('source')} — {title[:60]}"
            )
            self._log_news(news, cls)
        # LOW e NEUTRAL: silencioso

    def _send_telegram_alert(self, news: dict, cls: dict) -> None:
        """Envia alerta Telegram para notícia de alto impacto."""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from alerts import send_message

            impact   = cls.get("impact",    "NEUTRAL")
            conf     = cls.get("confidence", "?")
            reason   = cls.get("reason",     "")
            lock_min = int(cls.get("lock_minutes", 30))
            source   = news.get("source", "?")
            title    = news.get("title",  "")

            if impact == "BEARISH":
                msg = (
                    f"⚠️ ALERTA MACRO — BEARISH\n"
                    f"📰 {source}: {title}\n"
                    f"📊 Impacto: ALTO · Confiança: {conf}\n"
                    f"💬 {reason}\n"
                    f"🔒 Sinais travados por {lock_min} min"
                )
            else:  # BULLISH
                msg = (
                    f"📈 EVENTO MACRO — BULLISH\n"
                    f"📰 {source}: {title}\n"
                    f"📊 Impacto: ALTO · Confiança: {conf}\n"
                    f"💬 {reason}\n"
                    f"⏱ Monitorando por {lock_min} min"
                )

            send_message(msg)
            logger.info(f"[NewsSentinel] Alerta Telegram enviado: {impact} HIGH")
        except Exception as e:
            logger.error(f"[NewsSentinel] Falha ao enviar Telegram: {e}")

    def _log_news(self, news: dict, cls: dict) -> None:
        """Loga notícia classificada em logs/news_YYYY-MM-DD.jsonl."""
        try:
            LOGS_DIR.mkdir(exist_ok=True)
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_path = LOGS_DIR / f"news_{date_str}.jsonl"
            entry = {
                "ts":           datetime.now(timezone.utc).isoformat(),
                "title":        news.get("title",  ""),
                "source":       news.get("source", ""),
                "tier":         news.get("tier",   ""),
                "url":          news.get("url",    ""),
                "impact":       cls.get("impact",    "NEUTRAL"),
                "magnitude":    cls.get("magnitude", "LOW"),
                "confidence":   cls.get("confidence","LOW"),
                "reason":       cls.get("reason",    ""),
                "lock_minutes": cls.get("lock_minutes", 0),
                "score_adjust": cls.get("score_adjust", 0.0),
            }
            with log_path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.debug(f"[NewsSentinel] _log_news falhou: {e}")

    # ── Interface pública (usada pelo pipeline de sinais) ─────────────────────

    def check_signal_clearance(self, direction: str, score: float) -> dict:
        """
        Chamado antes de qualquer envio de sinal Telegram.
        Retorna:
          cleared      (bool)  — True se o sinal pode ser enviado
          reason       (str|None) — motivo do bloqueio
          multiplier   (float) — multiplicador a aplicar no score
          context      (str)   — descrição do estado macro
          locked_until (str|None) — ISO timestamp de liberação

        Fail-open: qualquer exceção interna → sinal aprovado com multiplier 1.0.
        """
        try:
            if self.is_locked():
                return {
                    "cleared":      False,
                    "reason":       "macro_lock",
                    "multiplier":   0.0,
                    "context":      self._state.get("lock_reason", "Evento macro ativo"),
                    "locked_until": self._state.get("locked_until"),
                }

            mult = self.get_score_multiplier()
            ctx  = self.get_macro_context_for_alert()

            return {
                "cleared":      True,
                "reason":       None,
                "multiplier":   mult,
                "context":      ctx or "Macro neutro — sem eventos de impacto",
                "locked_until": None,
            }
        except Exception as e:
            logger.error(f"[NewsSentinel] check_signal_clearance falhou (fail-open): {e}")
            return {
                "cleared":      True,
                "reason":       None,
                "multiplier":   1.0,
                "context":      None,
                "locked_until": None,
            }

    def get_macro_context_for_alert(self) -> Optional[str]:
        """
        String de contexto macro para incluir no alerta Telegram.
        Retorna None se sentimento for NEUTRAL.
        Exemplo: "⚠️ Macro bearish há 18 min: declaração geopolítica"
        """
        try:
            sentiment = self._state.get("current_sentiment", "NEUTRAL")
            if sentiment == "NEUTRAL":
                return None

            last = self._state.get("last_high_impact")
            if not last:
                return None

            reason     = last.get("reason", "")
            ts_str     = last.get("timestamp", "")
            time_label = ""
            if ts_str:
                try:
                    ts_dt = datetime.fromisoformat(ts_str)
                    if ts_dt.tzinfo is None:
                        ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                    delta      = datetime.now(timezone.utc) - ts_dt
                    mins_ago   = int(delta.total_seconds() // 60)
                    time_label = f" há {mins_ago} min"
                except Exception:
                    pass

            emoji = "⚠️" if sentiment == "BEARISH" else "📈"
            return f"{emoji} Macro {sentiment.lower()}{time_label}: {reason}"
        except Exception:
            return None

    # ── Propriedades para o endpoint /api/news-status ─────────────────────────

    @property
    def state_snapshot(self) -> dict:
        """Retorna cópia do estado atual para a API."""
        return dict(self._state)
