"""
Module 4 - Regime Gate Auto-Tuner

Ajusta direction x regime gate baseado em WR historico de cada combinacao.

Regras:
    WR < 30% -> BLOCK (nao alertar)
    WR 30-50% -> RESTRICT_SCORE (so score 40-55)
    WR > 50% -> ALLOW (passar normalmente)

Mudancas sempre conservadoras:
    - So aplica se min 100 samples e 14 dias de dados
    - So muda 1 regra por execucao (mais seguro)
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone

from trinity.auto_learning import safety, metrics, state

logger = logging.getLogger("auto_learning.regime_tuner")

MODULE_NAME = "REGIME_TUNE"


def _decide_rule(wr: float) -> str:
    """Decide regra baseada em WR."""
    if wr < 0.30:
        return "BLOCK"
    elif wr < 0.50:
        return "RESTRICT_SCORE"
    else:
        return "ALLOW"


def run() -> dict:
    """Executa regime tuner."""
    result = {
        "module": MODULE_NAME,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "enabled": False,
        "changes": [],
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
        days_data = metrics.get_days_of_data()
        if days_data < safety.REGIME_GATE_MIN_DAYS_DATA:
            result["status"] = "insufficient_days"
            result["days_data"] = days_data
            return result

        wr_combos = metrics.get_wr_by_direction_regime(
            days=30,
            min_trades=safety.REGIME_GATE_MIN_SAMPLES,
        )

        if not wr_combos:
            result["status"] = "insufficient_data"
            return result

        config = state.load_config()
        current_gate = config.get("regime_gate", {})

        candidates = []
        for (direction, regime), stats in wr_combos.items():
            key = f"{direction}_{regime}"
            current_rule = current_gate.get(key, "ALLOW")
            wr = stats["wr"]
            new_rule = _decide_rule(wr)

            if new_rule != current_rule:
                candidates.append({
                    "key": key,
                    "direction": direction,
                    "regime": regime,
                    "current_rule": current_rule,
                    "new_rule": new_rule,
                    "wr": wr,
                    "trades": stats["trades"],
                    "discrepancy": abs(wr - 0.40),
                })

        if not candidates:
            result["status"] = "no_changes_needed"
            return result

        candidates.sort(key=lambda c: -c["discrepancy"])
        change = candidates[0]

        ok, msg = safety.validate_regime_gate_change(
            direction=change["direction"],
            regime=change["regime"],
            samples=change["trades"],
            days_of_data=days_data,
        )

        if not ok:
            logger.info(f"[{MODULE_NAME}] Mudanca {change['key']} BLOQUEADA: {msg}")
            result["status"] = "validation_failed"
            result["errors"].append({"key": change["key"], "validation": msg})
            return result

        snapshot_path = safety.create_snapshot(
            reason=f"regime_gate_{change['key']}",
            config_data=config,
        )

        current_gate[change["key"]] = change["new_rule"]
        config["regime_gate"] = current_gate

        saved = state.save_config(config, snapshot=False, reason="regime_tuner")
        if not saved:
            result["errors"].append({"error": "save_failed"})
            return result

        change_record = {
            "key": change["key"],
            "direction": change["direction"],
            "regime": change["regime"],
            "before": change["current_rule"],
            "after": change["new_rule"],
            "wr": change["wr"],
            "trades": change["trades"],
        }

        safety.log_change(
            module=MODULE_NAME,
            change_type="regime_gate_update",
            details=change_record,
            sample_size=change["trades"],
            snapshot_path=snapshot_path,
        )

        result["changes"].append(change_record)
        result["status"] = "success"

        logger.info(
            f"[{MODULE_NAME}] {change['key']}: "
            f"{change['current_rule']} -> {change['new_rule']} "
            f"(WR={change['wr']:.1%}, n={change['trades']})"
        )

        safety.update_health(MODULE_NAME, "ok", {"changes": 1})

    except Exception as e:
        logger.exception(f"[{MODULE_NAME}] Erro inesperado")
        result["status"] = "error"
        result["errors"].append({"exception": str(e)})
        safety.update_health(MODULE_NAME, "error", {"exception": str(e)})

    return result
