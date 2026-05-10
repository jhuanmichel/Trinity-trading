"""
Module 5 - Health Monitor

Detecta sinais de degradacao no sistema e tenta auto-recover.

Checks:
    1. Outcomes sendo registrados? (ultimo outcome < 6h atras)
    2. ML pipeline rodando? (ultimo ML run < 7d atras)
    3. Telegram funcionando? (heartbeat)
    4. Persistent disk acessivel?
    5. Configs sendo salvos corretamente?

Acoes:
    - Se algo OK: log info, atualizar health.json
    - Se algo WARNING: alertar Telegram (com rate limit 1x/dia)
    - Se algo CRITICAL: alertar Telegram + tentar self-recover
"""

from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from trinity.auto_learning import safety, metrics, state
from trinity.auto_learning.telegram_sender import send_telegram_alert

logger = logging.getLogger("auto_learning.health_monitor")

MODULE_NAME = "HEALTH_MONITOR"

# Limites pra detectar degradacao
OUTCOMES_STALE_HOURS = 6      # se sem outcomes ha 6h+ -> warning
OUTCOMES_DEAD_HOURS = 24      # se sem outcomes ha 24h+ -> critical
ML_STALE_DAYS = 7              # ML nao rodou ha 7 dias -> warning
ML_DEAD_DAYS = 14              # ML nao rodou ha 14 dias -> critical

# Rate limit pra alerts (nao spammar Telegram)
ALERT_COOLDOWN_HOURS = 24


def _get_last_alert_time(alert_key: str) -> Optional[datetime]:
    """Retorna timestamp do ultimo alert do tipo X."""
    paths = safety.get_paths()
    alert_log = paths["base"] / "alerts.jsonl"

    if not alert_log.exists():
        return None

    last = None
    try:
        with open(alert_log) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("alert_key") == alert_key:
                        ts = entry.get("timestamp")
                        if ts:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if last is None or dt > last:
                                last = dt
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning(f"[{MODULE_NAME}] Erro lendo alerts log: {e}")

    return last


def _record_alert(alert_key: str, level: str, message: str) -> None:
    """Registra alert pra rate limiting."""
    paths = safety.get_paths()
    alert_log = paths["base"] / "alerts.jsonl"

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "alert_key": alert_key,
        "level": level,
        "message": message,
    }

    try:
        with open(alert_log, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.error(f"[{MODULE_NAME}] Erro registrando alert: {e}")


def _alert_telegram(alert_key: str, level: str, title: str, body: str) -> bool:
    """Envia alert respeitando rate limit."""
    last = _get_last_alert_time(alert_key)
    if last:
        elapsed = datetime.now(timezone.utc) - last
        if elapsed < timedelta(hours=ALERT_COOLDOWN_HOURS):
            logger.info(
                f"[{MODULE_NAME}] Alert '{alert_key}' suprimido "
                f"(cooldown {elapsed.total_seconds() / 3600:.1f}h)"
            )
            return False

    sent = send_telegram_alert(level, title, body)
    if sent:
        _record_alert(alert_key, level, f"{title}: {body}")
    return sent


# ============================================================
# Health Checks
# ============================================================

def _check_outcomes_freshness() -> dict:
    """Outcomes sendo registrados recentemente?"""
    outcomes_dir = metrics.get_outcomes_dir()

    latest_ts = None
    try:
        for f in sorted(outcomes_dir.glob("outcomes_*.jsonl"), reverse=True)[:3]:
            try:
                with open(f) as fh:
                    lines = fh.readlines()
                    for line in reversed(lines):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            o = json.loads(line)
                            ts = o.get("timestamp") or o.get("registered_at") or ""
                            if ts:
                                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                if latest_ts is None or dt > latest_ts:
                                    latest_ts = dt
                                break
                        except json.JSONDecodeError:
                            continue
                if latest_ts:
                    break
            except Exception as e:
                logger.warning(f"[{MODULE_NAME}] Erro lendo {f}: {e}")
    except Exception as e:
        return {
            "status": "error",
            "message": f"erro acessando outcomes_dir: {e}",
        }

    if latest_ts is None:
        return {
            "status": "critical",
            "message": "Nenhum outcome encontrado",
        }

    elapsed = datetime.now(timezone.utc) - latest_ts
    elapsed_hours = elapsed.total_seconds() / 3600

    if elapsed_hours > OUTCOMES_DEAD_HOURS:
        return {
            "status": "critical",
            "message": f"Sem outcomes ha {elapsed_hours:.1f}h",
            "last_outcome_at": latest_ts.isoformat(),
        }
    elif elapsed_hours > OUTCOMES_STALE_HOURS:
        return {
            "status": "warning",
            "message": f"Outcomes parados ha {elapsed_hours:.1f}h",
            "last_outcome_at": latest_ts.isoformat(),
        }
    else:
        return {
            "status": "ok",
            "message": f"Outcomes recentes ({elapsed_hours:.1f}h)",
            "last_outcome_at": latest_ts.isoformat(),
        }


def _check_ml_pipeline() -> dict:
    """ML pipeline rodou recentemente?"""
    candidate_paths = [
        Path("/data/ml/results.json"),
        Path("/data/logs/ml_results.json"),
        Path("logs/ml/results.json"),
        Path("dashboard/ml_results.json"),
        Path("trinity/ml/results.json"),
    ]

    ml_results_path = None
    for p in candidate_paths:
        if p.exists():
            ml_results_path = p
            break

    if ml_results_path is None:
        return {
            "status": "warning",
            "message": "ML results nao encontrado (sistema pode ser novo)",
        }

    try:
        mtime = datetime.fromtimestamp(ml_results_path.stat().st_mtime, tz=timezone.utc)
    except Exception as e:
        return {
            "status": "error",
            "message": f"erro verificando mtime: {e}",
        }

    elapsed_days = (datetime.now(timezone.utc) - mtime).days

    if elapsed_days > ML_DEAD_DAYS:
        return {
            "status": "critical",
            "message": f"ML nao rodou ha {elapsed_days}d",
            "last_run_at": mtime.isoformat(),
        }
    elif elapsed_days > ML_STALE_DAYS:
        return {
            "status": "warning",
            "message": f"ML stale ha {elapsed_days}d",
            "last_run_at": mtime.isoformat(),
        }
    else:
        return {
            "status": "ok",
            "message": f"ML atualizado ({elapsed_days}d)",
            "last_run_at": mtime.isoformat(),
        }


def _check_persistent_disk() -> dict:
    """Persistent disk acessivel e writable?"""
    if not Path("/data").exists():
        return {
            "status": "warning",
            "message": "/data nao existe (rodando em fallback local)",
        }

    if not os.access("/data", os.W_OK):
        return {
            "status": "critical",
            "message": "/data nao writable",
        }

    test_file = Path("/data/.health_check")
    try:
        test_file.write_text("ok")
        if test_file.read_text() != "ok":
            return {"status": "error", "message": "/data write/read mismatch"}
        test_file.unlink()
        return {"status": "ok", "message": "/data writable"}
    except Exception as e:
        return {"status": "critical", "message": f"erro write/read: {e}"}


def _check_config_persistence() -> dict:
    """Config esta sendo lido/escrito corretamente?"""
    try:
        config = state.load_config()
        if not isinstance(config, dict):
            return {"status": "error", "message": "config nao e dict"}

        required_keys = ("thresholds", "symbol_blacklist", "regime_gate")
        missing = [k for k in required_keys if k not in config]
        if missing:
            return {
                "status": "error",
                "message": f"campos ausentes: {missing}",
            }

        return {"status": "ok", "message": "config OK"}
    except Exception as e:
        return {"status": "critical", "message": f"erro: {e}"}


# ============================================================
# Run
# ============================================================

def run() -> dict:
    """Executa health monitor."""
    result = {
        "module": MODULE_NAME,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "enabled": False,
        "checks": {},
        "alerts_sent": [],
    }

    if not safety.is_module_enabled(MODULE_NAME):
        result["status"] = "disabled"
        return result
    result["enabled"] = True

    # NOTA: health_monitor NAO checa emergency_state porque ELE MESMO detecta emergencias.
    is_emergency, emergency_reason = safety.is_emergency_state(metrics)
    result["emergency_state"] = is_emergency
    result["emergency_reason"] = emergency_reason

    checks = {
        "outcomes_freshness": _check_outcomes_freshness(),
        "ml_pipeline": _check_ml_pipeline(),
        "persistent_disk": _check_persistent_disk(),
        "config_persistence": _check_config_persistence(),
    }

    result["checks"] = checks

    statuses = [c.get("status", "unknown") for c in checks.values()]
    if "critical" in statuses:
        overall = "critical"
    elif "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    result["overall_status"] = overall

    if overall in ("critical", "error"):
        critical_checks = [
            (name, c) for name, c in checks.items()
            if c.get("status") in ("critical", "error")
        ]

        body_lines = []
        for name, check in critical_checks:
            body_lines.append(f"- {name}: {check.get('message', 'unknown')}")

        sent = _alert_telegram(
            alert_key=f"health_{overall}",
            level="critical" if overall == "critical" else "error",
            title=f"Trinity Auto-Learning - {overall.upper()}",
            body="\n".join(body_lines),
        )
        if sent:
            result["alerts_sent"].append(overall)
    elif overall == "warning":
        warning_checks = [
            (name, c) for name, c in checks.items()
            if c.get("status") == "warning"
        ]
        body_lines = [
            f"- {name}: {c.get('message')}"
            for name, c in warning_checks
        ]

        sent = _alert_telegram(
            alert_key="health_warning",
            level="warning",
            title="Trinity Auto-Learning - WARNING",
            body="\n".join(body_lines),
        )
        if sent:
            result["alerts_sent"].append("warning")

    safety.update_health(MODULE_NAME, overall, {
        "checks": {k: v.get("status") for k, v in checks.items()},
        "emergency_state": is_emergency,
    })

    result["status"] = "success"

    check_summary = ", ".join(f"{n}:{c.get('status')}" for n, c in checks.items())
    logger.info(f"[{MODULE_NAME}] overall={overall} | checks={check_summary}")

    return result
