"""
Module 7 - Weekly Report

Envia relatorio semanal automatico no Telegram com:
    - WR da semana vs anterior
    - Mudancas automaticas aplicadas
    - Simbolos auto-blacklisted/whitelisted
    - Status de cada modulo
    - Alertas se algo precisa de atencao do operador

Roda domingo 20:00 UTC (configuravel).
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from trinity.auto_learning import safety, metrics, state
from trinity.auto_learning.telegram_sender import send_telegram_message

logger = logging.getLogger("auto_learning.weekly_report")

MODULE_NAME = "WEEKLY_REPORT"


def _format_pct(value: float, decimals: int = 1) -> str:
    return f"{value * 100:.{decimals}f}%"


def _format_change(before: float, after: float, decimals: int = 1) -> str:
    delta = after - before
    sign = "+" if delta >= 0 else ""
    return f"{before:.{decimals}f} -> {after:.{decimals}f} ({sign}{delta:.{decimals}f})"


def _build_report() -> str:
    """Constroi texto do relatorio."""
    week_iso = datetime.now(timezone.utc).strftime("%Y-W%V")
    lines = []

    # Header
    lines.append(f"<b>Trinity Weekly Report - {week_iso}</b>")
    lines.append(f"<i>{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}</i>")
    lines.append("")

    # SECTION 1: Performance
    lines.append("=== PERFORMANCE ===")

    try:
        wr_7d = metrics.get_overall_wr(days=7)
        wr_14d = metrics.get_overall_wr(days=14)

        lines.append(
            f"WR 7d: <b>{_format_pct(wr_7d['wr'])}</b> "
            f"({wr_7d['wins']}W/{wr_7d['losses']}L = {wr_7d['total_resolved']})"
        )

        if wr_14d["total_resolved"] > wr_7d["total_resolved"]:
            wr_prev_week_total = wr_14d["total_resolved"] - wr_7d["total_resolved"]
            wr_prev_week_wins = wr_14d["wins"] - wr_7d["wins"]
            wr_prev_week_pct = wr_prev_week_wins / wr_prev_week_total if wr_prev_week_total > 0 else 0
            lines.append(f"WR semana anterior: {_format_pct(wr_prev_week_pct)}")

            delta = wr_7d["wr"] - wr_prev_week_pct
            if delta > 0.02:
                lines.append(f"[UP] Melhora: +{delta * 100:.1f}pp")
            elif delta < -0.02:
                lines.append(f"[DOWN] Queda: {delta * 100:.1f}pp")
            else:
                lines.append(f"[==] Estavel (delta {delta * 100:+.1f}pp)")
    except Exception as e:
        lines.append(f"Erro lendo WR: {e}")

    lines.append("")

    # SECTION 2: WR por dia
    try:
        recent_days = metrics.get_recent_wr_by_day(days=7)
        if recent_days:
            lines.append("WR diario (7d):")
            for d in recent_days:
                bar = "#" * int(d["wr"] * 20)
                lines.append(
                    f"  {d['date']}: {_format_pct(d['wr'], 0):>5} "
                    f"({d['trades']:>3}) {bar}"
                )
            lines.append("")
    except Exception as e:
        logger.warning(f"[{MODULE_NAME}] Erro WR diario: {e}")

    # SECTION 3: Volume
    try:
        vol = metrics.get_volume_stats(days=7)
        lines.append("=== VOLUME 7D ===")
        lines.append(f"Total: {vol['total']} sinais")
        lines.append(f"Resolvidos: {vol['resolved']}")
        lines.append(f"Pendentes: {vol['pending']}")
        lines.append(f"Media/dia: {vol['avg_per_day']:.1f}")
        lines.append("")
    except Exception as e:
        logger.warning(f"[{MODULE_NAME}] Erro volume: {e}")

    # SECTION 4: Auto-Learning Changes
    lines.append("=== AUTO-LEARNING ===")

    try:
        recent_changes = safety.get_recent_changes(days=7)
        if not recent_changes:
            lines.append("Nenhuma mudanca automatica nos ultimos 7d")
        else:
            by_module = {}
            for c in recent_changes:
                mod = c.get("module", "unknown")
                by_module.setdefault(mod, []).append(c)

            for mod in sorted(by_module.keys()):
                changes = by_module[mod]
                lines.append(f"\n<b>{mod}</b> - {len(changes)} mudanca(s):")

                for c in changes[-3:]:
                    ts = c.get("timestamp", "")[:10]
                    ct = c.get("change_type", "")
                    details = c.get("details", {})

                    if mod == "THRESHOLD_TUNE":
                        sig = details.get("signal_type", "")
                        before = details.get("before", 0)
                        after = details.get("after", 0)
                        lines.append(
                            f"  - {ts} {sig}: {_format_change(before, after, 0)}"
                        )
                    elif mod == "SYMBOL_MANAGE":
                        bl_added = len(details.get("blacklist_added", []))
                        bl_removed = len(details.get("blacklist_removed", []))
                        wl_added = len(details.get("whitelist_added", []))
                        wl_removed = len(details.get("whitelist_removed", []))
                        lines.append(
                            f"  - {ts} BL+{bl_added}/-{bl_removed} "
                            f"WL+{wl_added}/-{wl_removed}"
                        )
                    elif mod == "REGIME_TUNE":
                        key = details.get("key", "")
                        before = details.get("before", "")
                        after = details.get("after", "")
                        lines.append(f"  - {ts} {key}: {before} -> {after}")
                    elif mod == "ML_APPLY":
                        sharpe = details.get("sharpe", 0)
                        lines.append(f"  - {ts} ML pesos aplicados (Sharpe {sharpe:.2f})")
                    else:
                        lines.append(f"  - {ts} {ct}")
    except Exception as e:
        lines.append(f"Erro lendo changes: {e}")

    lines.append("")

    # SECTION 5: Estado dos Modulos
    lines.append("=== MODULOS ===")
    try:
        health = safety.get_health()
        if not health:
            lines.append("Nenhum modulo reportou health ainda")
        else:
            for module_name in sorted(health.keys()):
                info = health[module_name]
                status = info.get("status", "unknown")
                tag = {
                    "ok": "[OK]",
                    "warning": "[WARN]",
                    "error": "[ERR]",
                    "critical": "[CRIT]",
                    "killed": "[KILLED]",
                }.get(status, "[?]")
                last_run = info.get("last_run", "")[:16]
                lines.append(f"{tag} {module_name}: {status} ({last_run})")
    except Exception as e:
        lines.append(f"Erro lendo health: {e}")

    lines.append("")

    # SECTION 6: Config Atual
    try:
        summary = state.get_config_summary()
        lines.append("=== CONFIG ATUAL ===")
        thresholds = summary.get("thresholds", {})
        lines.append(
            f"Threshold pump/crash: {thresholds.get('pump', '?')}/{thresholds.get('crash', '?')}"
        )
        lines.append(f"Blacklist: {summary.get('blacklist_count', 0)} simbolos")
        lines.append(f"Whitelist: {summary.get('whitelist_count', 0)} simbolos")
        lines.append(f"Sources silenciadas: {summary.get('disabled_sources_count', 0)}")
        ml_applied = summary.get("ml_weights_applied", False)
        lines.append(f"ML pesos aplicados: {'YES' if ml_applied else 'NO'}")
        lines.append("")
    except Exception as e:
        lines.append(f"Erro lendo config: {e}")

    # SECTION 7: Alertas pro operador
    alerts = []

    try:
        from trinity.auto_learning.performance_guard import is_killed_by_guard
        if is_killed_by_guard():
            alerts.append("PERFORMANCE GUARD ATIVADO - auto-learning desativado")
    except Exception:
        pass

    try:
        wr_7d = metrics.get_overall_wr(days=7)
        if wr_7d["total_resolved"] >= 50 and wr_7d["wr"] < 0.40:
            alerts.append(f"WR semanal baixa: {_format_pct(wr_7d['wr'])}")
    except Exception:
        pass

    try:
        health = safety.get_health()
        for mod, info in health.items():
            if info.get("status") in ("critical", "killed"):
                alerts.append(f"{mod}: {info.get('status')}")
    except Exception:
        pass

    if alerts:
        lines.append("=== ALERTAS ===")
        for a in alerts:
            lines.append(a)
        lines.append("")

    lines.append("<i>Proximo report: domingo 20:00 UTC</i>")

    return "\n".join(lines)


def _save_report(report_text: str) -> str:
    """Salva report em arquivo pra historico."""
    paths = safety.get_paths()
    reports_dir = paths["base"] / "weekly_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    week_iso = datetime.now(timezone.utc).strftime("%Y-W%V")
    report_path = reports_dir / f"{week_iso}.txt"

    try:
        with open(report_path, "w") as f:
            f.write(report_text)
        return str(report_path)
    except Exception as e:
        logger.error(f"[{MODULE_NAME}] Erro salvando report: {e}")
        return ""


def run() -> dict:
    """Executa weekly report."""
    result = {
        "module": MODULE_NAME,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "enabled": False,
        "sent": False,
    }

    if not safety.is_module_enabled(MODULE_NAME):
        result["status"] = "disabled"
        return result
    result["enabled"] = True

    try:
        report_text = _build_report()
        result["report_length"] = len(report_text)

        saved_path = _save_report(report_text)
        if saved_path:
            result["saved_to"] = saved_path

        # Telegram limita a 4096 chars por mensagem
        if len(report_text) > 4000:
            parts = []
            current = []
            current_len = 0
            for line in report_text.split("\n"):
                line_len = len(line) + 1
                if current_len + line_len > 3800:
                    parts.append("\n".join(current))
                    current = [line]
                    current_len = line_len
                else:
                    current.append(line)
                    current_len += line_len
            if current:
                parts.append("\n".join(current))

            all_sent = True
            for i, part in enumerate(parts, 1):
                header = f"[Parte {i}/{len(parts)}]\n" if len(parts) > 1 else ""
                sent = send_telegram_message(header + part, parse_mode="HTML")
                if not sent:
                    all_sent = False
                    break

            result["sent"] = all_sent
            result["parts"] = len(parts)
        else:
            result["sent"] = send_telegram_message(report_text, parse_mode="HTML")

        if result["sent"]:
            logger.info(f"[{MODULE_NAME}] Report enviado")
            safety.update_health(MODULE_NAME, "ok", {"sent": True})
            result["status"] = "success"
        else:
            logger.error(f"[{MODULE_NAME}] Falha enviando report")
            safety.update_health(MODULE_NAME, "warning", {"sent": False})
            result["status"] = "send_failed"

    except Exception as e:
        logger.exception(f"[{MODULE_NAME}] Erro inesperado")
        result["status"] = "error"
        result["error"] = str(e)
        safety.update_health(MODULE_NAME, "error", {"exception": str(e)})

    return result
