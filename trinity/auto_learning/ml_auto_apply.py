"""
Module 1 - ML Auto-Apply

Aplica pesos do ML automaticamente quando condicoes de seguranca batem:
    - Sharpe >= 1.5
    - Samples >= 1000
    - Walk-forward stability >= 0.6
    - Sistema NAO em emergencia

Conservador: so aplica 1 conjunto de pesos por semana no maximo.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from trinity.auto_learning import safety, metrics, state

logger = logging.getLogger("auto_learning.ml_auto_apply")

MODULE_NAME = "ML_APPLY"

APPLY_COOLDOWN_HOURS = 24 * 7  # 1 semana


def _read_ml_results() -> Optional[dict]:
    """
    Le resultado do ML pipeline.
    Tenta multiplos paths (mais novos primeiro).
    """
    candidate_paths = [
        Path("/data/ml/results.json"),
        Path("/data/logs/ml_results.json"),
        Path("logs/ml/results.json"),
        Path("dashboard/ml_results.json"),
        Path("trinity/ml/results.json"),
    ]

    for p in candidate_paths:
        if p.exists():
            try:
                with open(p) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[{MODULE_NAME}] Erro lendo {p}: {e}")
                continue

    return None


def _extract_metrics(ml_results: dict) -> dict:
    """
    Extrai metricas relevantes do output do ML pipeline.
    Adapta a multiplos formatos possiveis (defensivo).
    """
    out = {
        "sharpe": 0.0,
        "samples": 0,
        "walk_forward_stable": False,
        "stability_score": 0.0,
        "weights": {},
    }

    if "sharpe" in ml_results:
        out["sharpe"] = float(ml_results.get("sharpe", 0) or 0)
    elif "monte_carlo" in ml_results:
        mc = ml_results["monte_carlo"]
        out["sharpe"] = float(mc.get("sharpe", 0) or 0)

    out["samples"] = int(ml_results.get("n_total", ml_results.get("samples", 0)) or 0)

    wf = ml_results.get("walk_forward", {})
    if isinstance(wf, dict):
        out["stability_score"] = float(wf.get("stability", 0) or 0)
        out["walk_forward_stable"] = bool(
            wf.get("recommend", False) or out["stability_score"] >= 0.6
        )

    weights = ml_results.get("recommended_weights", ml_results.get("weights", {}))
    if isinstance(weights, dict):
        out["weights"] = weights

    return out


def _can_apply_now() -> tuple[bool, str]:
    """Verifica cooldown."""
    config = state.load_config()
    last_applied = config.get("ml_weights_applied_at")

    if not last_applied:
        return (True, "first_application")

    try:
        last_dt = datetime.fromisoformat(last_applied.replace("Z", "+00:00"))
        elapsed = datetime.now(timezone.utc) - last_dt
        if elapsed < timedelta(hours=APPLY_COOLDOWN_HOURS):
            return (
                False,
                f"cooldown ({elapsed.total_seconds() / 3600:.0f}h < {APPLY_COOLDOWN_HOURS}h)",
            )
    except Exception:
        pass

    return (True, "cooldown_ok")


def run() -> dict:
    """Executa ML auto-apply."""
    result = {
        "module": MODULE_NAME,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "enabled": False,
        "applied": False,
        "errors": [],
    }

    if not safety.is_module_enabled(MODULE_NAME):
        result["status"] = "disabled"
        return result
    result["enabled"] = True

    is_emergency, emergency_reason = safety.is_emergency_state(metrics)
    if is_emergency:
        safety.log_kill(MODULE_NAME, f"emergency_state: {emergency_reason}", {})
        result["status"] = "emergency_killed"
        return result

    try:
        can_apply, cooldown_reason = _can_apply_now()
        if not can_apply:
            result["status"] = "cooldown"
            result["cooldown_reason"] = cooldown_reason
            return result

        ml_results = _read_ml_results()
        if not ml_results:
            result["status"] = "no_ml_results"
            safety.update_health(MODULE_NAME, "warning", {"reason": "no_ml_results"})
            return result

        m = _extract_metrics(ml_results)
        result["ml_metrics"] = m

        ok, validation_msg = safety.validate_ml_apply_conditions(
            sharpe=m["sharpe"],
            samples=m["samples"],
            walk_forward_stable=m["walk_forward_stable"],
            stability_score=m["stability_score"],
        )

        if not ok:
            result["status"] = "conditions_not_met"
            result["validation"] = validation_msg
            logger.info(f"[{MODULE_NAME}] Condicoes nao atendidas: {validation_msg}")
            safety.update_health(MODULE_NAME, "warning", {"validation": validation_msg})
            return result

        if not m["weights"]:
            result["status"] = "no_weights"
            return result

        config = state.load_config()
        snapshot_path = safety.create_snapshot(
            reason="ml_weights_apply",
            config_data=config,
        )

        config["ml_weights_applied"] = True
        config["ml_weights_applied_at"] = datetime.now(timezone.utc).isoformat()
        config["ml_weights_data"] = m["weights"]

        saved = state.save_config(config, snapshot=False, reason="ml_auto_apply")
        if not saved:
            result["errors"].append({"error": "save_failed"})
            return result

        change_record = {
            "sharpe": m["sharpe"],
            "samples": m["samples"],
            "stability": m["stability_score"],
            "weights": m["weights"],
        }

        safety.log_change(
            module=MODULE_NAME,
            change_type="ml_weights_applied",
            details=change_record,
            sample_size=m["samples"],
            snapshot_path=snapshot_path,
        )

        result["applied"] = True
        result["status"] = "success"

        logger.info(
            f"[{MODULE_NAME}] Pesos aplicados: Sharpe={m['sharpe']:.2f}, "
            f"samples={m['samples']}, stability={m['stability_score']:.2f}"
        )

        safety.update_health(MODULE_NAME, "ok", change_record)

    except Exception as e:
        logger.exception(f"[{MODULE_NAME}] Erro inesperado")
        result["status"] = "error"
        result["errors"].append({"exception": str(e)})
        safety.update_health(MODULE_NAME, "error", {"exception": str(e)})

    return result
