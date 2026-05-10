"""
Module 6 - Performance Guard

Master kill switch baseado em performance.

Se WR despencar abaixo de FLOOR por DAYS dias consecutivos:
    - Desabilita TODOS os modulos auto-learning (env vars)
    - Envia alerta CRITICAL no Telegram
    - Sistema volta pro modo "estagnado mas seguro"

Filosofia: melhor congelar config atual do que continuar mudando
quando tudo ta indo mal.
"""

from __future__ import annotations
import logging
import os
from datetime import datetime, timezone

from trinity.auto_learning import safety, metrics
from trinity.auto_learning.telegram_sender import send_telegram_alert

logger = logging.getLogger("auto_learning.performance_guard")

MODULE_NAME = "PERFORMANCE_GUARD"


def _disable_all_modules_via_state() -> bool:
    """
    Desabilita modulos via flag no current_config.json.

    NOTA: nao conseguimos mudar env vars do Render via codigo,
    mas podemos setar uma flag no config que os modulos checam.
    """
    from trinity.auto_learning import state

    config = state.load_config()
    flags = config.get("flags", {})

    if flags.get("auto_learning_killed_by_guard"):
        return False  # ja estava desligado

    snapshot_path = safety.create_snapshot(
        reason="performance_guard_kill",
        config_data=config,
    )

    flags["auto_learning_killed_by_guard"] = True
    flags["auto_learning_killed_at"] = datetime.now(timezone.utc).isoformat()
    config["flags"] = flags

    saved = state.save_config(config, snapshot=False, reason="performance_guard")

    if saved:
        safety.log_change(
            module=MODULE_NAME,
            change_type="auto_learning_killed",
            details={"reason": "wr_emergency"},
            snapshot_path=snapshot_path,
        )

    return saved


def is_killed_by_guard() -> bool:
    """
    Outros modulos podem chamar isso pra verificar se foram killed pelo guard.
    """
    from trinity.auto_learning import state
    config = state.load_config()
    return bool(config.get("flags", {}).get("auto_learning_killed_by_guard"))


def reset_kill_flag() -> bool:
    """
    Reseta a flag de kill (uso manual via console - operador deve avaliar).
    NAO e chamado automaticamente.
    """
    from trinity.auto_learning import state

    config = state.load_config()
    flags = config.get("flags", {})

    if not flags.get("auto_learning_killed_by_guard"):
        return False

    snapshot_path = safety.create_snapshot(
        reason="performance_guard_reset",
        config_data=config,
    )

    flags["auto_learning_killed_by_guard"] = False
    flags["auto_learning_unkilled_at"] = datetime.now(timezone.utc).isoformat()
    config["flags"] = flags

    saved = state.save_config(config, snapshot=False, reason="performance_guard_reset")
    if saved:
        safety.log_change(
            module=MODULE_NAME,
            change_type="auto_learning_reset",
            details={"manual": True},
            snapshot_path=snapshot_path,
        )
    return saved


def run() -> dict:
    """Executa performance guard."""
    result = {
        "module": MODULE_NAME,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "enabled": False,
    }

    if not safety.is_module_enabled(MODULE_NAME):
        result["status"] = "disabled"
        return result
    result["enabled"] = True

    try:
        if is_killed_by_guard():
            result["status"] = "already_killed"
            return result

        is_emergency, reason = safety.is_emergency_state(metrics)
        result["is_emergency"] = is_emergency
        result["reason"] = reason

        if not is_emergency:
            result["status"] = "ok"
            safety.update_health(MODULE_NAME, "ok", {"reason": "no_emergency"})
            return result

        # EMERGENCIA - disparar kill switch
        logger.critical(f"[{MODULE_NAME}] PERFORMANCE EMERGENCY: {reason}")

        killed = _disable_all_modules_via_state()
        result["killed"] = killed

        recent_wr = metrics.get_recent_wr_by_day(days=7)
        wr_summary = []
        for d in recent_wr[-5:]:
            wr_summary.append(
                f"  - {d['date']}: WR {d['wr']:.1%} ({d['trades']} trades)"
            )

        body = (
            f"<b>Performance Guard ATIVADO</b>\n\n"
            f"Razao: {reason}\n\n"
            f"WR ultimos dias:\n"
            f"{chr(10).join(wr_summary)}\n\n"
            f"Auto-learning DESATIVADO.\n"
            f"Sistema volta pra modo manual."
        )

        sent = send_telegram_alert(
            level="critical",
            title="TRINITY AUTO-LEARNING KILLED",
            body=body,
        )
        result["alert_sent"] = sent

        safety.log_kill(MODULE_NAME, reason, {
            "wr_summary": [(d["date"], d["wr"]) for d in recent_wr[-5:]],
        })

        safety.update_health(MODULE_NAME, "killed", {"reason": reason})

        result["status"] = "killed"

    except Exception as e:
        logger.exception(f"[{MODULE_NAME}] Erro inesperado")
        result["status"] = "error"
        result["error"] = str(e)
        safety.update_health(MODULE_NAME, "error", {"exception": str(e)})

    return result
