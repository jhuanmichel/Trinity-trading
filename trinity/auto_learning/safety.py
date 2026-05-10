"""
Camada de seguranca do auto-learning.

Toda mudanca automatica DEVE passar por aqui antes de ser aplicada.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("auto_learning.safety")


# ============================================================
# CAPS ABSOLUTOS - nunca podem ser cruzados
# ============================================================

# Threshold (ALERT_THRESHOLD)
THRESHOLD_MIN = 25
THRESHOLD_MAX = 90
THRESHOLD_MAX_CHANGE_PER_RUN = 2     # max mudanca por execucao do tuner
THRESHOLD_MAX_CHANGE_PER_WEEK = 5    # max mudanca acumulada em 7 dias

# Symbol lists (blacklist/whitelist)
BLACKLIST_MAX_SIZE = 100
WHITELIST_MAX_SIZE = 50
SYMBOL_LIST_MAX_CHANGE_PER_RUN = 5     # max adicoes+remocoes por execucao
SYMBOL_LIST_MAX_CHANGE_PER_WEEK = 15

# ML weights apply
ML_APPLY_MIN_SAMPLES = 1000
ML_APPLY_MIN_SHARPE = 1.5
ML_APPLY_MIN_STABILITY = 0.6        # walk-forward stability score
ML_APPLY_REQUIRE_STABLE = True

# Regime gate tuning
REGIME_GATE_MIN_SAMPLES = 100
REGIME_GATE_MIN_DAYS_DATA = 14

# Source filter
SOURCE_FILTER_MIN_SAMPLES = 100
SOURCE_FILTER_WR_THRESHOLD = 0.45    # silenciar se WR < 45%


# ============================================================
# EMERGENCY KILL THRESHOLDS
# ============================================================

EMERGENCY_WR_FLOOR = 0.30           # WR abaixo disso = emergencia
EMERGENCY_DAYS_BELOW = 3            # dias consecutivos abaixo do floor
EMERGENCY_MIN_TRADES_PER_DAY = 30   # min trades/dia pra avaliar


# ============================================================
# PATHS
# ============================================================

def get_data_dir() -> Path:
    """
    Diretorio base do auto-learning.
    Usa /data/auto_learning se persistent disk disponivel,
    fallback pra logs/auto_learning.
    """
    if Path("/data").exists() and os.access("/data", os.W_OK):
        base = Path("/data/auto_learning")
    else:
        base = Path("logs/auto_learning")
        logger.warning(
            "[SAFETY] /data nao disponivel ou nao writable - "
            "usando logs/auto_learning. ATENCAO: dados podem ser perdidos no deploy!"
        )

    base.mkdir(parents=True, exist_ok=True)
    return base


def get_paths() -> dict[str, Path]:
    """Retorna paths importantes do auto-learning."""
    base = get_data_dir()
    snapshots_dir = base / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    return {
        "base": base,
        "changes_log": base / "changes.jsonl",
        "snapshots": snapshots_dir,
        "current_config": base / "current_config.json",
        "health": base / "health.json",
        "kill_log": base / "kill_switch_log.jsonl",
        "weekly_reports": base / "weekly_reports",
    }


# ============================================================
# KILL SWITCHES
# ============================================================

# Mapeamento module_name -> env var
MODULE_ENV_VARS = {
    "ML_APPLY": "AUTO_ML_APPLY",
    "THRESHOLD_TUNE": "AUTO_THRESHOLD_TUNE",
    "SYMBOL_MANAGE": "AUTO_SYMBOL_MANAGE",
    "REGIME_TUNE": "AUTO_REGIME_TUNE",
    "HEALTH_MONITOR": "AUTO_HEALTH_MONITOR",
    "PERFORMANCE_GUARD": "AUTO_PERFORMANCE_GUARD",
    "WEEKLY_REPORT": "AUTO_WEEKLY_REPORT",
}

# Stage 1 activation (2026-05-10): master + health_monitor habilitados por DEFAULT.
# Pra DESATIVAR: setar AUTO_LEARNING_ENABLED=false no Render env, ou reverter
# este commit. Adicionar mais modulos aqui conforme avanca a sequencia de
# ativacao (vide AUTOLEARNING_README ou prompt de ativacao progressiva).
DEFAULT_MASTER_ENABLED = "true"
DEFAULT_ENABLED_MODULES = {"HEALTH_MONITOR"}


def _env_truthy(value: str) -> bool:
    """Converte string env var pra bool (true/1/yes/on)."""
    if not value:
        return False
    return value.strip().lower() in ("true", "1", "yes", "on", "enabled")


def is_module_enabled(module_name: str) -> bool:
    """
    Verifica se modulo esta habilitado.

    Master switch: AUTO_LEARNING_ENABLED=false desliga TUDO.
    Per-module switch: AUTO_<MODULE>=false desliga so esse modulo.

    Default per-module: false, EXCETO modulos em DEFAULT_ENABLED_MODULES
    (ativacao progressiva em codigo). Override via env var sempre vence.
    """
    # Master switch
    master = os.getenv("AUTO_LEARNING_ENABLED", DEFAULT_MASTER_ENABLED)
    if not _env_truthy(master):
        logger.debug(f"[SAFETY] Master switch OFF: AUTO_LEARNING_ENABLED={master}")
        return False

    # Per-module switch
    var_name = MODULE_ENV_VARS.get(module_name, f"AUTO_{module_name}")
    default_for_module = "true" if module_name in DEFAULT_ENABLED_MODULES else "false"
    module_value = os.getenv(var_name, default_for_module)
    if not _env_truthy(module_value):
        logger.debug(f"[SAFETY] Module {module_name} OFF: {var_name}={module_value}")
        return False

    return True


# ============================================================
# EMERGENCY STATE CHECK
# ============================================================

def is_emergency_state(metrics_module=None) -> tuple[bool, str]:
    """
    Verifica se sistema esta em estado de emergencia.

    Em emergencia, NENHUMA mudanca automatica deve ser aplicada.

    Args:
        metrics_module: modulo metrics (passado por dependencia pra evitar circular import)

    Retorna (is_emergency, reason).
    """
    # Em duvida = emergencia (fail-safe)
    if metrics_module is None:
        try:
            from trinity.auto_learning import metrics as metrics_module
        except ImportError as e:
            logger.error(f"[SAFETY] Nao consegui importar metrics: {e}")
            return (True, f"metrics_import_failed: {e}")

    try:
        recent_wr = metrics_module.get_recent_wr_by_day(days=EMERGENCY_DAYS_BELOW + 1)
    except Exception as e:
        logger.error(f"[SAFETY] Erro lendo WR recente: {e}")
        return (True, f"wr_check_failed: {e}")

    if not recent_wr or len(recent_wr) < EMERGENCY_DAYS_BELOW:
        # Sem dados suficientes - nao considerar emergencia
        return (False, "insufficient_data")

    # Pegar ultimos N dias completos
    last_n_days = recent_wr[-EMERGENCY_DAYS_BELOW:]

    # Todos abaixo do floor com volume suficiente?
    all_below = True
    for day in last_n_days:
        if day["trades"] < EMERGENCY_MIN_TRADES_PER_DAY:
            all_below = False
            break
        if day["wr"] >= EMERGENCY_WR_FLOOR:
            all_below = False
            break

    if all_below:
        avg_wr = sum(d["wr"] for d in last_n_days) / len(last_n_days)
        avg_trades = sum(d["trades"] for d in last_n_days) / len(last_n_days)
        reason = (
            f"WR media {EMERGENCY_DAYS_BELOW}d = {avg_wr:.1%} < {EMERGENCY_WR_FLOOR:.0%} "
            f"(avg {avg_trades:.0f} trades/dia)"
        )
        return (True, reason)

    return (False, "wr_acceptable")


# ============================================================
# SNAPSHOTS - backup antes de mudar
# ============================================================

def create_snapshot(reason: str, config_data: dict) -> Optional[str]:
    """
    Salva snapshot do config ANTES de aplicar mudanca.

    Args:
        reason: identificador curto (ex: "threshold_change", "blacklist_add")
        config_data: dict com config atual

    Retorna path do snapshot ou None se falhou.
    """
    paths = get_paths()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # Sanitize reason pra filename
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)
    snapshot_path = paths["snapshots"] / f"{timestamp}_{safe_reason}.json"

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "config": config_data,
    }

    try:
        with open(snapshot_path, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)
        logger.info(f"[SAFETY] Snapshot criado: {snapshot_path.name}")
        return str(snapshot_path)
    except Exception as e:
        logger.error(f"[SAFETY] Falha criando snapshot {reason}: {e}")
        return None


def cleanup_old_snapshots(retention_days: int = 30) -> int:
    """Remove snapshots > retention_days dias. Retorna quantos foram removidos."""
    paths = get_paths()
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    removed = 0
    try:
        for snapshot in paths["snapshots"].glob("*.json"):
            try:
                mtime = datetime.fromtimestamp(snapshot.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    snapshot.unlink()
                    removed += 1
            except Exception as e:
                logger.warning(f"[SAFETY] Erro removendo snapshot {snapshot}: {e}")
    except Exception as e:
        logger.error(f"[SAFETY] Erro listando snapshots: {e}")

    if removed:
        logger.info(f"[SAFETY] {removed} snapshots > {retention_days}d removidos")
    return removed


# ============================================================
# AUDIT LOG - registro de toda mudanca
# ============================================================

def log_change(
    module: str,
    change_type: str,
    details: dict,
    sample_size: int = 0,
    snapshot_path: Optional[str] = None,
) -> None:
    """
    Registra mudanca automatica em changes.jsonl.

    Args:
        module: nome do modulo (ex: "THRESHOLD_TUNE")
        change_type: tipo da mudanca (ex: "threshold_up", "blacklist_add")
        details: dict com detalhes (before/after/reason)
        sample_size: quantos outcomes motivaram a decisao
        snapshot_path: path do snapshot pre-mudanca (pra rollback)
    """
    paths = get_paths()

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "module": module,
        "change_type": change_type,
        "details": details,
        "sample_size": sample_size,
        "snapshot_path": snapshot_path,
    }

    try:
        with open(paths["changes_log"], "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        logger.info(f"[SAFETY] Change: {module}/{change_type} (samples={sample_size})")
    except Exception as e:
        logger.error(f"[SAFETY] Falha logando change {module}/{change_type}: {e}")


def get_recent_changes(module: Optional[str] = None, days: int = 7) -> list[dict]:
    """
    Retorna mudancas recentes do audit log.

    Args:
        module: filtrar por modulo (None = todos)
        days: janela em dias
    """
    paths = get_paths()

    if not paths["changes_log"].exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()

    changes = []
    try:
        with open(paths["changes_log"]) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("timestamp", "") < cutoff_iso:
                        continue
                    if module and entry.get("module") != module:
                        continue
                    changes.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.error(f"[SAFETY] Erro lendo changes log: {e}")

    return changes


def count_changes_in_period(
    module: str,
    change_type: str,
    days: int = 7,
) -> int:
    """Conta quantas mudancas do tipo X no modulo Y nos ultimos N dias."""
    changes = get_recent_changes(module=module, days=days)
    return sum(1 for c in changes if c.get("change_type") == change_type)


# ============================================================
# KILL LOG - quando kill switch e acionado
# ============================================================

def log_kill(module: str, reason: str, context: dict) -> None:
    """Registra acionamento de kill switch."""
    paths = get_paths()

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "module": module,
        "reason": reason,
        "context": context,
    }

    try:
        with open(paths["kill_log"], "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        logger.warning(f"[SAFETY] KILL: {module} - {reason}")
    except Exception as e:
        logger.error(f"[SAFETY] Falha logando kill {module}: {e}")


# ============================================================
# VALIDACOES - usadas pelos modulos antes de aplicar mudancas
# ============================================================

def validate_threshold_change(
    current: float,
    proposed: float,
    module_name: str = "THRESHOLD_TUNE",
) -> tuple[bool, str]:
    """
    Valida mudanca em ALERT_THRESHOLD.

    Verifica:
        - proposed dentro de [MIN, MAX]
        - mudanca <= MAX_CHANGE_PER_RUN
        - mudancas acumuladas em 7d <= MAX_CHANGE_PER_WEEK
    """
    if proposed < THRESHOLD_MIN:
        return (False, f"proposed {proposed:.1f} < min {THRESHOLD_MIN}")
    if proposed > THRESHOLD_MAX:
        return (False, f"proposed {proposed:.1f} > max {THRESHOLD_MAX}")

    change = abs(proposed - current)
    if change > THRESHOLD_MAX_CHANGE_PER_RUN:
        return (False, f"change {change:.1f} > max_per_run {THRESHOLD_MAX_CHANGE_PER_RUN}")

    # Verificar acumulado em 7d
    recent = get_recent_changes(module=module_name, days=7)
    accumulated = sum(
        abs(c.get("details", {}).get("delta", 0))
        for c in recent
        if c.get("change_type", "").startswith("threshold")
    )
    if accumulated + change > THRESHOLD_MAX_CHANGE_PER_WEEK:
        return (
            False,
            f"acumulado 7d {accumulated:.1f} + {change:.1f} > "
            f"max_per_week {THRESHOLD_MAX_CHANGE_PER_WEEK}"
        )

    return (True, "valid")


def validate_symbol_list_change(
    current_set: set[str],
    new_set: set[str],
    list_type: str,
    module_name: str = "SYMBOL_MANAGE",
) -> tuple[bool, str]:
    """
    Valida mudanca em blacklist ou whitelist.

    Args:
        list_type: "blacklist" ou "whitelist"
    """
    max_size = BLACKLIST_MAX_SIZE if list_type == "blacklist" else WHITELIST_MAX_SIZE

    if len(new_set) > max_size:
        return (False, f"new size {len(new_set)} > max {max_size} ({list_type})")

    additions = new_set - current_set
    removals = current_set - new_set
    total_changes = len(additions) + len(removals)

    if total_changes == 0:
        return (True, "no_changes")

    if total_changes > SYMBOL_LIST_MAX_CHANGE_PER_RUN:
        return (
            False,
            f"changes {total_changes} > max_per_run {SYMBOL_LIST_MAX_CHANGE_PER_RUN}"
        )

    # Acumulado 7d
    recent = get_recent_changes(module=module_name, days=7)
    accumulated = sum(
        c.get("details", {}).get("changes_count", 0)
        for c in recent
    )
    if accumulated + total_changes > SYMBOL_LIST_MAX_CHANGE_PER_WEEK:
        return (
            False,
            f"acumulado 7d {accumulated} + {total_changes} > "
            f"max_per_week {SYMBOL_LIST_MAX_CHANGE_PER_WEEK}"
        )

    return (True, f"valid: +{len(additions)} -{len(removals)}")


def validate_ml_apply_conditions(
    sharpe: float,
    samples: int,
    walk_forward_stable: bool,
    stability_score: float = 0.0,
) -> tuple[bool, str]:
    """Valida se condicoes pra aplicar pesos ML estao OK."""
    if samples < ML_APPLY_MIN_SAMPLES:
        return (False, f"samples {samples} < min {ML_APPLY_MIN_SAMPLES}")

    if sharpe < ML_APPLY_MIN_SHARPE:
        return (False, f"sharpe {sharpe:.2f} < min {ML_APPLY_MIN_SHARPE}")

    if ML_APPLY_REQUIRE_STABLE and not walk_forward_stable:
        return (False, "walk_forward_not_stable")

    if stability_score and stability_score < ML_APPLY_MIN_STABILITY:
        return (
            False,
            f"stability {stability_score:.2f} < min {ML_APPLY_MIN_STABILITY}"
        )

    return (True, "valid")


def validate_regime_gate_change(
    direction: str,
    regime: str,
    samples: int,
    days_of_data: int,
) -> tuple[bool, str]:
    """Valida mudanca em direction x regime gate."""
    if direction not in ("LONG", "SHORT"):
        return (False, f"invalid direction: {direction}")

    valid_regimes = (
        "STRONG_BEAR", "BEAR", "NEUTRAL", "BULL", "STRONG_BULL", "UNKNOWN"
    )
    if regime not in valid_regimes:
        return (False, f"invalid regime: {regime}")

    if samples < REGIME_GATE_MIN_SAMPLES:
        return (False, f"samples {samples} < min {REGIME_GATE_MIN_SAMPLES}")

    if days_of_data < REGIME_GATE_MIN_DAYS_DATA:
        return (False, f"days {days_of_data} < min {REGIME_GATE_MIN_DAYS_DATA}")

    return (True, "valid")


# ============================================================
# HEALTH STATUS - estado geral do auto-learning
# ============================================================

def update_health(
    module: str,
    status: str,
    details: dict,
) -> None:
    """
    Atualiza health.json com status de cada modulo.

    Args:
        module: nome do modulo
        status: "ok" / "warning" / "error" / "killed"
        details: dict com info adicional
    """
    paths = get_paths()

    health = {}
    if paths["health"].exists():
        try:
            with open(paths["health"]) as f:
                health = json.load(f)
        except Exception as e:
            logger.warning(f"[SAFETY] Erro lendo health.json: {e}")
            health = {}

    health[module] = {
        "status": status,
        "last_run": datetime.now(timezone.utc).isoformat(),
        "details": details,
    }

    try:
        with open(paths["health"], "w") as f:
            json.dump(health, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"[SAFETY] Falha salvando health.json: {e}")


def get_health() -> dict:
    """Retorna estado de health atual."""
    paths = get_paths()
    if not paths["health"].exists():
        return {}
    try:
        with open(paths["health"]) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[SAFETY] Erro lendo health.json: {e}")
        return {}
