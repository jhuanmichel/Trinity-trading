"""
Orchestrator - roda todos os modulos auto-learning no schedule certo.

Schedule:
    DAILY (1x por dia, qualquer hora):
        - health_monitor
        - performance_guard

    WEEKLY - domingo:
        - 06:00-19:59 UTC: threshold/symbol/regime/ml_apply
        - 20:00+ UTC: weekly_report
"""

from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from trinity.auto_learning import safety
from trinity.auto_learning import (
    threshold_tuner,
    symbol_manager,
    regime_tuner,
    ml_auto_apply,
    health_monitor,
    performance_guard,
    weekly_report,
)

logger = logging.getLogger("auto_learning.orchestrator")


def _load_last_runs() -> dict:
    """Carrega last_runs do disco."""
    paths = safety.get_paths()
    p = paths["base"] / "last_runs.json"
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[ORCHESTRA] Erro lendo last_runs: {e}")
        return {}


def _save_last_runs(data: dict) -> None:
    """Salva last_runs no disco (atomic)."""
    paths = safety.get_paths()
    p = paths["base"] / "last_runs.json"
    try:
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(p)
    except Exception as e:
        logger.error(f"[ORCHESTRA] Erro salvando last_runs: {e}")


def _ran_today(module_name: str, last_runs: dict) -> bool:
    """Ja rodou hoje (UTC)?"""
    last = last_runs.get(module_name)
    if not last:
        return False
    today = datetime.now(timezone.utc).date().isoformat()
    return last.startswith(today)


def _ran_this_week(module_name: str, last_runs: dict) -> bool:
    """Ja rodou nessa semana ISO (UTC)?"""
    last = last_runs.get(module_name)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        return False

    now = datetime.now(timezone.utc)
    return last_dt.isocalendar()[:2] == now.isocalendar()[:2]


def _record_run(module_name: str, last_runs: dict) -> None:
    """Registra que modulo rodou agora."""
    last_runs[module_name] = datetime.now(timezone.utc).isoformat()


def _safe_run(module_name: str, run_func, result: dict) -> None:
    """Executa modulo capturando excecoes."""
    try:
        logger.info(f"[ORCHESTRA] Executando {module_name}")
        module_result = run_func()
        result.setdefault("results", {})[module_name] = module_result
        result["modules_ran"].append(module_name)
    except Exception as e:
        logger.exception(f"[ORCHESTRA] Erro em {module_name}")
        result.setdefault("results", {})[module_name] = {
            "status": "exception",
            "error": str(e),
        }


def run_orchestrator(force: bool = False) -> dict:
    """
    Roda modulos que devem rodar AGORA.

    Args:
        force: ignora schedule (uso manual via endpoint)
    """
    result = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "force": force,
        "master_enabled": False,
        "modules_ran": [],
        "modules_skipped": [],
    }

    # Master kill switch (default segue safety.DEFAULT_MASTER_ENABLED)
    master = os.getenv("AUTO_LEARNING_ENABLED", safety.DEFAULT_MASTER_ENABLED)
    if master.lower() not in ("true", "1", "yes", "on"):
        result["status"] = "master_disabled"
        return result
    result["master_enabled"] = True

    # Performance guard ativo?
    try:
        if performance_guard.is_killed_by_guard() and not force:
            result["status"] = "killed_by_guard"
            return result
    except Exception as e:
        logger.warning(f"[ORCHESTRA] Erro checando guard: {e}")

    last_runs = _load_last_runs()
    now = datetime.now(timezone.utc)
    is_sunday = now.weekday() == 6
    hour = now.hour

    # === DAILY MODULES ===
    daily_modules = [
        ("HEALTH_MONITOR", health_monitor.run),
        ("PERFORMANCE_GUARD", performance_guard.run),
    ]

    for module_name, run_func in daily_modules:
        if force or not _ran_today(module_name, last_runs):
            _safe_run(module_name, run_func, result)
            _record_run(module_name, last_runs)
        else:
            result["modules_skipped"].append(f"{module_name}:already_ran_today")

    # === WEEKLY - domingo manha ===
    weekly_morning = [
        ("ML_APPLY", ml_auto_apply.run),
        ("THRESHOLD_TUNE", threshold_tuner.run),
        ("SYMBOL_MANAGE", symbol_manager.run),
        ("REGIME_TUNE", regime_tuner.run),
    ]

    if force or (is_sunday and 6 <= hour < 20):
        for module_name, run_func in weekly_morning:
            if force or not _ran_this_week(module_name, last_runs):
                _safe_run(module_name, run_func, result)
                _record_run(module_name, last_runs)
            else:
                result["modules_skipped"].append(f"{module_name}:already_ran_this_week")
    else:
        for module_name, _ in weekly_morning:
            result["modules_skipped"].append(f"{module_name}:not_sunday_morning")

    # === WEEKLY REPORT - domingo 20:00+ UTC ===
    if force or (is_sunday and hour >= 20):
        if force or not _ran_this_week("WEEKLY_REPORT", last_runs):
            _safe_run("WEEKLY_REPORT", weekly_report.run, result)
            _record_run("WEEKLY_REPORT", last_runs)
        else:
            result["modules_skipped"].append("WEEKLY_REPORT:already_ran_this_week")
    else:
        result["modules_skipped"].append("WEEKLY_REPORT:not_sunday_evening")

    _save_last_runs(last_runs)

    # Cleanup snapshots antigos (1x/dia)
    if force or not _ran_today("CLEANUP", last_runs):
        try:
            removed = safety.cleanup_old_snapshots(retention_days=30)
            result["snapshots_cleaned"] = removed
            _record_run("CLEANUP", last_runs)
            _save_last_runs(last_runs)
        except Exception as e:
            logger.warning(f"[ORCHESTRA] Erro no cleanup: {e}")

    result["status"] = "completed"

    logger.info(
        f"[ORCHESTRA] ran={result['modules_ran']} | "
        f"skipped={len(result['modules_skipped'])}"
    )

    return result


def get_orchestrator_status() -> dict:
    """Status atual (pra endpoint de monitoramento)."""
    last_runs = _load_last_runs()
    now = datetime.now(timezone.utc)

    status = {
        "current_time": now.isoformat(),
        "is_sunday": now.weekday() == 6,
        "hour_utc": now.hour,
        "master_enabled": os.getenv("AUTO_LEARNING_ENABLED", safety.DEFAULT_MASTER_ENABLED).lower() in ("true", "1", "yes", "on"),
        "last_runs": last_runs,
    }

    try:
        status["killed_by_guard"] = performance_guard.is_killed_by_guard()
    except Exception:
        status["killed_by_guard"] = "unknown"

    status["modules_enabled"] = {}
    for module_name in [
        "ML_APPLY", "THRESHOLD_TUNE", "SYMBOL_MANAGE", "REGIME_TUNE",
        "HEALTH_MONITOR", "PERFORMANCE_GUARD", "WEEKLY_REPORT",
    ]:
        status["modules_enabled"][module_name] = safety.is_module_enabled(module_name)

    return status
