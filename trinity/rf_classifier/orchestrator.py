"""
Orchestrator - schedule de retreino diario.

Roda 1x por dia (3h UTC = madrugada) pra retreinar modelos com outcomes novos.
Usa kill switch independente.
"""

from __future__ import annotations
import logging
import os
from datetime import datetime, timezone

from trinity.rf_classifier.trainer import train_all_sources
from trinity.rf_classifier.inference import reset_cache

logger = logging.getLogger("rf_classifier.orchestrator")


def is_retrain_enabled() -> bool:
    """Retreino automatico habilitado?"""
    master = os.getenv("RF_CLASSIFIER_ENABLED", "false")
    if master.lower() not in ("true", "1", "yes", "on"):
        return False

    val = os.getenv("RF_AUTO_RETRAIN", "false")
    return val.lower() in ("true", "1", "yes", "on")


def run_retrain() -> dict:
    """Executa retreino + reset de cache."""
    result = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "enabled": is_retrain_enabled(),
    }

    if not result["enabled"]:
        result["status"] = "disabled"
        return result

    try:
        train_result = train_all_sources()
        result["train"] = train_result
        result["status"] = train_result.get("status", "unknown")

        # Reset cache pra carregar novos modelos
        reset_cache()
        result["cache_reset"] = True

    except Exception as e:
        logger.exception("[ORCH_RF] Erro em retreino")
        result["status"] = "exception"
        result["error"] = str(e)

    return result
