"""
Module 2 - Threshold Tuner

Ajusta ALERT_THRESHOLD por signal_type (pump/crash) baseado em WR por faixa.

Logica:
    - Se faixa 35-45 tem WR alto (>65%) e faixa 65-75 tem WR baixo (<40%):
        score discrimina ao contrario (overextension)
        BAIXAR threshold pra capturar zona lucrativa (35-65)
    - Se zona alta (>80) ainda tem WR > 60%:
        nao mexer (sistema funciona normal)
    - Mudanca maxima: 2 pontos por execucao, 5 por semana
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone

from trinity.auto_learning import safety, metrics, state

logger = logging.getLogger("auto_learning.threshold_tuner")

MODULE_NAME = "THRESHOLD_TUNE"


def _decide_new_threshold(
    current: float,
    wr_by_band: dict,
    signal_type: str,
) -> tuple[float, str]:
    """
    Decide novo threshold baseado em WR por faixa.

    Retorna (new_threshold, reason).
    Se reason == "no_change", manter threshold atual.
    """
    bands_data = {}
    for (lo, hi), stats in wr_by_band.items():
        bands_data[(lo, hi)] = {
            "wr": stats["wr"],
            "trades": stats["trades"],
        }

    profitable_bands = [
        (lo, hi) for (lo, hi), d in bands_data.items()
        if d["wr"] >= 0.60 and d["trades"] >= 30
    ]

    bad_bands = [
        (lo, hi) for (lo, hi), d in bands_data.items()
        if d["wr"] < 0.40 and d["trades"] >= 30
    ]

    current_band = None
    for (lo, hi) in bands_data.keys():
        if lo <= current < hi:
            current_band = (lo, hi)
            break

    if not current_band or current_band not in bands_data:
        return (current, "no_band_data")

    current_wr = bands_data[current_band]["wr"]

    # Caso 1: faixa atual tem WR ruim, e existe faixa LUCRATIVA mais BAIXA
    if current_wr < 0.45 and profitable_bands:
        target_high = max(hi for (lo, hi) in profitable_bands)
        if target_high < current:
            delta = -min(safety.THRESHOLD_MAX_CHANGE_PER_RUN, current - target_high)
            new_t = current + delta
            return (new_t, f"shift_to_profitable_band_{target_high}")

    # Caso 2: faixa atual tem WR muito BOM (>70%) - nao mudar
    if current_wr > 0.70:
        return (current, "current_band_excellent")

    # Caso 3: faixa imediatamente acima ou abaixo e melhor
    sorted_bands = sorted(bands_data.keys())
    try:
        idx = sorted_bands.index(current_band)
    except ValueError:
        return (current, "current_band_not_found")

    # Banda acima
    if idx + 1 < len(sorted_bands):
        upper_band = sorted_bands[idx + 1]
        upper_wr = bands_data[upper_band]["wr"]
        if upper_wr > current_wr + 0.10 and bands_data[upper_band]["trades"] >= 30:
            delta = min(safety.THRESHOLD_MAX_CHANGE_PER_RUN, upper_band[0] - current)
            if delta > 0:
                new_t = current + delta
                return (new_t, f"shift_up_to_better_band_{upper_band[0]}")

    # Banda abaixo
    if idx > 0:
        lower_band = sorted_bands[idx - 1]
        lower_wr = bands_data[lower_band]["wr"]
        if lower_wr > current_wr + 0.10 and bands_data[lower_band]["trades"] >= 30:
            delta = -min(safety.THRESHOLD_MAX_CHANGE_PER_RUN, current - lower_band[1])
            if delta < 0:
                new_t = current + delta
                return (new_t, f"shift_down_to_better_band_{lower_band[1]}")

    return (current, "no_better_band_found")


def run() -> dict:
    """Executa threshold tuner."""
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
        safety.update_health(MODULE_NAME, "killed", {"reason": emergency_reason})
        result["status"] = "emergency_killed"
        result["emergency_reason"] = emergency_reason
        return result

    try:
        wr_by_band = metrics.get_wr_by_score_band(days=14, min_trades=30)

        if not wr_by_band:
            result["status"] = "insufficient_data"
            safety.update_health(MODULE_NAME, "warning", {
                "reason": "no_score_band_data"
            })
            return result

        config = state.load_config()
        thresholds = config.get("thresholds", {})

        for signal_type in ("pump", "crash"):
            current = float(thresholds.get(signal_type, 80))
            new_threshold, reason = _decide_new_threshold(
                current=current,
                wr_by_band=wr_by_band,
                signal_type=signal_type,
            )

            if abs(new_threshold - current) < 0.5:
                continue

            ok, validation_msg = safety.validate_threshold_change(
                current=current,
                proposed=new_threshold,
                module_name=MODULE_NAME,
            )

            if not ok:
                logger.info(
                    f"[{MODULE_NAME}] {signal_type}: {current:.1f}->{new_threshold:.1f} "
                    f"BLOQUEADO ({validation_msg})"
                )
                result["errors"].append({
                    "signal_type": signal_type,
                    "validation": validation_msg,
                })
                continue

            snapshot_path = safety.create_snapshot(
                reason=f"threshold_change_{signal_type}",
                config_data=config,
            )

            old = current
            thresholds[signal_type] = new_threshold
            config["thresholds"] = thresholds

            saved = state.save_config(config, snapshot=False, reason="threshold_tuner")
            if not saved:
                result["errors"].append({
                    "signal_type": signal_type,
                    "error": "save_failed",
                })
                continue

            change_record = {
                "signal_type": signal_type,
                "before": old,
                "after": new_threshold,
                "delta": new_threshold - old,
                "reason": reason,
            }

            safety.log_change(
                module=MODULE_NAME,
                change_type=f"threshold_{'up' if new_threshold > old else 'down'}",
                details=change_record,
                sample_size=sum(d["trades"] for d in wr_by_band.values()),
                snapshot_path=snapshot_path,
            )

            result["changes"].append(change_record)
            logger.info(
                f"[{MODULE_NAME}] {signal_type}: {old:.1f}->{new_threshold:.1f} ({reason})"
            )

        result["status"] = "success"
        safety.update_health(MODULE_NAME, "ok", {
            "changes": len(result["changes"]),
            "errors": len(result["errors"]),
        })

    except Exception as e:
        logger.exception(f"[{MODULE_NAME}] Erro inesperado")
        result["status"] = "error"
        result["errors"].append({"exception": str(e)})
        safety.update_health(MODULE_NAME, "error", {"exception": str(e)})

    return result
