"""
outcome_health_monitor.py — Monitora se outcomes estão sendo registrados.

Verifica a cada 1h:
  - Se houve outcomes novos nas últimas 2h
  - Se o volume diário caiu abaixo do esperado
  - Quais fontes estão ativas vs silenciosas

Alerta no Telegram se:
  - Nenhum outcome novo em ALERT_IF_NO_OUTCOMES_MINUTES minutos
  - Volume diário < 5 com histórico de 100+ outcomes

Estrutura real dos arquivos (descoberta via diagnóstico §0):
  logs/pending_outcomes.jsonl  — 1 JSON por linha, campo "registered_at" | "timestamp"
  logs/outcomes_2026-04.jsonl  — 1 JSON por linha (resolvidos), campo "resolved_at"
"""
import json
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
from typing import Optional, Callable

log = logging.getLogger(__name__)


class OutcomeHealthMonitor:
    """Verifica saúde do registro de outcomes e alerta via Telegram se parar."""

    ALERT_IF_NO_OUTCOMES_MINUTES = 120  # alertar se 2h sem outcomes
    CHECK_INTERVAL_MINUTES       = 60   # checar a cada 1h (usado pelo scheduler)

    def __init__(
        self,
        pending_path: str = "logs/pending_outcomes.jsonl",
        telegram_sender: Optional[Callable[[str], bool]] = None,
    ):
        self._pending_path = Path(pending_path)
        self._send         = telegram_sender
        self._last_alert   = 0.0
        self._alert_cd     = 6 * 3600   # cooldown 6h entre alertas
        self._last_status: dict = {}

    # ── Carregar ───────────────────────────────────────────────────────────────

    def _load(self) -> list:
        """Carrega JSONL de outcomes. Robusto a linhas malformadas."""
        if not self._pending_path.exists():
            return []
        items = []
        with open(self._pending_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return items

    # ── Timestamp helper ───────────────────────────────────────────────────────

    @staticmethod
    def _ts(obj: dict) -> float:
        """Extrai timestamp Unix de um outcome. Tenta vários campos."""
        for key in ("registered_at", "timestamp", "created_at", "detected_at"):
            val = obj.get(key)
            if not val:
                continue
            try:
                if isinstance(val, (int, float)):
                    return float(val)
                return datetime.fromisoformat(
                    str(val).replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError):
                pass
        return 0.0

    # ── Verificação principal ──────────────────────────────────────────────────

    def check(self) -> dict:
        """
        Roda verificação completa.
        Retorna status dict. Envia alerta Telegram se necessário.
        """
        try:
            items = self._load()
            now   = time.time()

            if not items:
                self._alert(
                    "🔴 OUTCOME MONITOR\n\n"
                    "Arquivo de outcomes VAZIO ou não encontrado.\n"
                    f"Caminho: {self._pending_path}\n\n"
                    "Scanners estão registrando sinais?"
                )
                status = {"status": "error", "reason": "no_outcomes_file", "total": 0}
                self._last_status = status
                return status

            # ── Timestamp mais recente ─────────────────────────────────────────
            latest_ts   = 0.0
            latest_sym  = "?"
            latest_src  = "?"
            for o in items:
                ts = self._ts(o)
                if ts > latest_ts:
                    latest_ts  = ts
                    latest_sym = o.get("symbol", "?")
                    latest_src = o.get("source", o.get("trader", o.get("radar", "?")))

            age_min = (now - latest_ts) / 60 if latest_ts > 0 else 9999

            # ── Contagem últimas 24h ───────────────────────────────────────────
            cutoff_24h = now - 86400
            sources_24h: dict = defaultdict(int)
            last_24h = 0

            for o in items:
                ts = self._ts(o)
                if ts >= cutoff_24h:
                    last_24h += 1
                    src = o.get("source", o.get("trader", o.get("radar", "unknown")))
                    sources_24h[str(src).lower()] += 1

            # ── Build status ───────────────────────────────────────────────────
            healthy = age_min < self.ALERT_IF_NO_OUTCOMES_MINUTES
            age_str = (
                f"{age_min:.0f}min" if age_min < 60
                else f"{age_min/60:.1f}h"  if age_min < 1440
                else f"{age_min/1440:.1f}d"
            )

            status = {
                "status":              "ok" if healthy else "stale",
                "total":               len(items),
                "last_24h":            last_24h,
                "latest_symbol":       latest_sym,
                "latest_source":       latest_src,
                "latest_age_minutes":  round(age_min, 1),
                "latest_age_str":      age_str,
                "sources_24h":         dict(sources_24h),
                "healthy":             healthy,
                "checked_at":          now,
            }

            # ── Alertas ────────────────────────────────────────────────────────
            if age_min >= self.ALERT_IF_NO_OUTCOMES_MINUTES:
                msg = (
                    f"🔴 OUTCOME MONITOR\n\n"
                    f"Nenhum outcome novo há {age_str}!\n\n"
                    f"Último: {latest_sym} ({latest_src})\n"
                    f"Total 24h: {last_24h}\n"
                    f"Fontes: {dict(sources_24h) or 'nenhuma'}\n\n"
                    f"Verificar: scanners rodando? OutcomeTracker salvando?"
                )
                self._alert(msg)
                status["alert_sent"] = True

            elif last_24h < 5 and len(items) > 100:
                # Tem histórico mas volume colapsou
                msg = (
                    f"🟡 OUTCOME MONITOR\n\n"
                    f"Apenas {last_24h} outcomes nas últimas 24h\n"
                    f"(esperado: 50+, histórico total: {len(items)})\n\n"
                    f"Fontes: {dict(sources_24h)}\n"
                    f"Último: {latest_sym} há {age_str}"
                )
                self._alert(msg)
                status["alert_sent"] = True

            else:
                log.info(
                    f"[OutcomeHealth] OK: {len(items)} total, {last_24h} 24h, "
                    f"último há {age_str} ({latest_sym})"
                )

            self._last_status = status
            return status

        except Exception as e:
            log.error(f"[OutcomeHealth] Erro na verificação: {e}", exc_info=True)
            status = {"status": "error", "error": str(e)}
            self._last_status = status
            return status

    def get_status(self) -> dict:
        """Retorna último status sem re-verificar."""
        return dict(self._last_status)

    # ── Alerta interno ─────────────────────────────────────────────────────────

    def _alert(self, message: str) -> None:
        """Envia alerta Telegram com cooldown de 6h."""
        now = time.time()
        if now - self._last_alert < self._alert_cd:
            log.debug("[OutcomeHealth] Alerta suprimido (cooldown 6h)")
            return

        if self._send is None:
            # Tentar importar automaticamente
            try:
                from alerts import send_message
                self._send = send_message
            except Exception:
                log.warning("[OutcomeHealth] Telegram não disponível — alerta apenas em log")
                log.warning(f"[OutcomeHealth] ALERTA: {message}")
                return

        try:
            ok = self._send(message)
            if ok:
                self._last_alert = now
                log.info("[OutcomeHealth] Alerta Telegram enviado")
            else:
                log.warning("[OutcomeHealth] Telegram retornou falso")
        except Exception as e:
            log.error(f"[OutcomeHealth] Erro ao enviar Telegram: {e}")


# ── Singleton ──────────────────────────────────────────────────────────────────

_monitor: Optional[OutcomeHealthMonitor] = None


def get_monitor(pending_path: str = "logs/pending_outcomes.jsonl") -> OutcomeHealthMonitor:
    """Retorna instância singleton do OutcomeHealthMonitor."""
    global _monitor
    if _monitor is None:
        _monitor = OutcomeHealthMonitor(pending_path=pending_path)
    return _monitor
