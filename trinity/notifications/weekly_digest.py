"""
trinity/notifications/weekly_digest.py
Digest semanal de performance enviado todo domingo às 20:00 UTC.

Métricas calculadas (stdlib puro, zero dependências externas):
  - Trades da semana: W/L, win rate, P&L estimado
  - Comparação com semana anterior (tendência ↑/↓/→)
  - Top símbolo da semana (maior nº de trades)
  - Win rate acumulado geral

Carregamento: usa _load_resolved_outcomes() de trinity.ml.feature_importance
Envio Telegram: usa send_message() de alerts (parse_mode HTML)
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Estimativas de P&L quando pnl_pct não está disponível
WIN_PNL_FALLBACK  = 2.2   # %
LOSS_PNL_FALLBACK = -1.5  # %

# Tolerância para "semana estável" (sem tendência clara)
STABILITY_TOLERANCE_PCT = 2.0


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_ts(ts: str) -> datetime.datetime | None:
    """Parseia ISO 8601 para datetime UTC. Retorna None se inválido."""
    if not ts:
        return None
    try:
        # Remove 'Z' e trata como UTC
        ts = ts.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(ts)
    except Exception:
        return None


def _outcome_ts(o: dict) -> datetime.datetime | None:
    """Extrai timestamp do outcome (resolved_at preferível, fallback timestamp)."""
    for key in ("resolved_at", "timestamp"):
        dt = _parse_ts(o.get(key, ""))
        if dt is not None:
            return dt
    return None


def _pnl(o: dict) -> float:
    """P&L de um outcome: usa pnl_pct se disponível, senão estimativa."""
    if "pnl_pct" in o:
        try:
            return float(o["pnl_pct"])
        except (TypeError, ValueError):
            pass
    return WIN_PNL_FALLBACK if o.get("status") == "WIN" else LOSS_PNL_FALLBACK


def _filter_window(outcomes: list[dict], start: datetime.datetime, end: datetime.datetime) -> list[dict]:
    """Filtra outcomes resolvidos dentro da janela [start, end)."""
    result = []
    for o in outcomes:
        if o.get("status") not in ("WIN", "LOSS"):
            continue
        dt = _outcome_ts(o)
        if dt is None:
            continue
        # Garante timezone-aware para comparação
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        if start <= dt < end:
            result.append(o)
    return result


def _weekly_stats(outcomes: list[dict]) -> dict[str, Any]:
    """Calcula métricas de um conjunto de outcomes."""
    if not outcomes:
        return {
            "n": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "pnl": 0.0,
            "top_symbol": None,
        }

    wins   = [o for o in outcomes if o.get("status") == "WIN"]
    losses = [o for o in outcomes if o.get("status") == "LOSS"]
    n      = len(outcomes)

    pnl = sum(_pnl(o) for o in outcomes)

    # Símbolo mais frequente
    sym_count: dict[str, int] = {}
    for o in outcomes:
        sym = o.get("symbol", "")
        if sym:
            sym_count[sym] = sym_count.get(sym, 0) + 1
    top_sym = max(sym_count, key=sym_count.get) if sym_count else None

    return {
        "n":          n,
        "wins":       len(wins),
        "losses":     len(losses),
        "win_rate":   round(len(wins) / n * 100, 1) if n > 0 else 0.0,
        "pnl":        round(pnl, 1),
        "top_symbol": top_sym,
    }


def _trend_arrow(this_wr: float, prev_wr: float) -> str:
    """Retorna ↑/↓/→ comparando win rates."""
    delta = this_wr - prev_wr
    if delta > STABILITY_TOLERANCE_PCT:
        return "↑"
    if delta < -STABILITY_TOLERANCE_PCT:
        return "↓"
    return "→"


class WeeklyDigest:
    """
    Gera e envia o digest semanal de performance Trinity.
    READ-ONLY: nunca modifica outcomes nem scoring engines.
    """

    def __init__(
        self,
        telegram_sender: Callable[[str], bool] | None = None,
        outcomes_loader: Callable[[], list[dict]] | None = None,
    ) -> None:
        if telegram_sender is None:
            from alerts import send_message as _sm
            telegram_sender = _sm
        if outcomes_loader is None:
            from trinity.ml.feature_importance import _load_resolved_outcomes
            outcomes_loader = _load_resolved_outcomes

        self._send  = telegram_sender
        self._load  = outcomes_loader

    # ------------------------------------------------------------------
    def generate(self) -> dict[str, Any]:
        """
        Calcula métricas da semana atual e anterior.
        Retorna dict com stats_this, stats_prev, cumulative, timestamp.
        """
        now   = _utcnow()
        week_end   = now
        week_start = now - datetime.timedelta(days=7)
        prev_start = now - datetime.timedelta(days=14)

        all_outcomes = self._load()

        this_week = _filter_window(all_outcomes, week_start, week_end)
        prev_week = _filter_window(all_outcomes, prev_start, week_start)
        all_resolved = [o for o in all_outcomes if o.get("status") in ("WIN", "LOSS")]

        stats_this = _weekly_stats(this_week)
        stats_prev = _weekly_stats(prev_week)

        all_wins   = sum(1 for o in all_resolved if o.get("status") == "WIN")
        all_n      = len(all_resolved)
        cum_wr     = round(all_wins / all_n * 100, 1) if all_n > 0 else 0.0
        cum_pnl    = round(sum(_pnl(o) for o in all_resolved), 1)

        return {
            "timestamp":  now.isoformat(),
            "stats_this": stats_this,
            "stats_prev": stats_prev,
            "cumulative": {
                "n":        all_n,
                "wins":     all_wins,
                "losses":   all_n - all_wins,
                "win_rate": cum_wr,
                "pnl":      cum_pnl,
            },
        }

    # ------------------------------------------------------------------
    def format_message(self, data: dict[str, Any]) -> str:
        """Formata o digest como mensagem HTML para Telegram."""
        this = data["stats_this"]
        prev = data["stats_prev"]
        cum  = data["cumulative"]

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

        # Tendência vs semana anterior
        if prev["n"] > 0:
            arrow = _trend_arrow(this["win_rate"], prev["win_rate"])
            prev_line = (
                f"Semana anterior: {prev['wins']}W/{prev['losses']}L "
                f"({prev['win_rate']}%) {arrow}"
            )
        else:
            prev_line = "Semana anterior: sem dados"

        # Linha de P&L
        pnl_str = f"+{this['pnl']:.1f}%" if this['pnl'] >= 0 else f"{this['pnl']:.1f}%"

        # Top símbolo
        top_line = f"Top símbolo: <b>{this['top_symbol']}</b>" if this.get("top_symbol") else ""

        # Bloco principal
        if this["n"] == 0:
            body = (
                "<i>Sem trades resolvidos esta semana.</i>\n\n"
                f"Win rate cumulativo: <b>{cum['win_rate']}%</b> ({cum['n']} trades totais)"
            )
        else:
            body = (
                f"Trades: <b>{this['n']}</b> ({this['wins']}W / {this['losses']}L)\n"
                f"Win rate: <b>{this['win_rate']}%</b>\n"
                f"P&amp;L estimado: <b>{pnl_str}</b>\n"
                + (f"{top_line}\n" if top_line else "")
                + f"\n{prev_line}\n\n"
                f"<b>Acumulado:</b> {cum['win_rate']}% WR | {cum['n']} trades | "
                + (f"+{cum['pnl']:.1f}%" if cum['pnl'] >= 0 else f"{cum['pnl']:.1f}%")
            )

        return (
            f"📊 <b>TRINITY — DIGEST SEMANAL</b>\n"
            f"<code>{'━' * 22}</code>\n"
            f"{body}\n\n"
            f"<i>{now_str}</i>"
        )

    # ------------------------------------------------------------------
    def send(self) -> dict[str, Any]:
        """Gera, formata e envia o digest. Retorna status dict."""
        try:
            data    = self.generate()
            msg     = self.format_message(data)
            success = self._send(msg)
            logger.info(
                "[WeeklyDigest] Enviado. n_this=%d, wr=%s%%, success=%s",
                data["stats_this"]["n"],
                data["stats_this"]["win_rate"],
                success,
            )
            return {"status": "ok" if success else "send_error", "data": data}
        except Exception as e:
            logger.exception("[WeeklyDigest] Erro ao gerar/enviar digest: %s", e)
            return {"status": "error", "error": str(e)}
