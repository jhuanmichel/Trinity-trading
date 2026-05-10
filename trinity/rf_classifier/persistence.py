"""
Persistence - salva e carrega modelos RF + metadata.

Formato:
    /data/rf/models/<source>.pkl       - modelo serializado (joblib)
    /data/rf/metadata.json             - metrics de cada source
    /data/rf/feature_columns.json      - schema das features
"""

from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("rf_classifier.persistence")


def get_rf_data_dir() -> Path:
    """Diretorio base do RF (persistent disk preferido)."""
    if Path("/data").exists() and os.access("/data", os.W_OK):
        base = Path("/data/rf")
    else:
        base = Path("logs/rf")
        logger.warning(
            "[PERSIST] /data nao disponivel - usando logs/rf "
            "(NAO sobrevive deploys!)"
        )

    base.mkdir(parents=True, exist_ok=True)
    (base / "models").mkdir(exist_ok=True)
    return base


def get_paths() -> dict[str, Path]:
    """Retorna paths importantes."""
    base = get_rf_data_dir()
    return {
        "base": base,
        "models_dir": base / "models",
        "metadata": base / "metadata.json",
        "feature_columns": base / "feature_columns.json",
        "observations": base / "observations.jsonl",
        "retrain_log": base / "retrain_log.jsonl",
    }


def _safe_source_name(source: str) -> str:
    """Sanitize source pra filename."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in source)


def save_model(source: str, model: Any, scaler: Any = None) -> bool:
    """
    Salva modelo + scaler em /data/rf/models/<source>.pkl.
    Atomic write (escreve em .tmp e renomeia).
    """
    try:
        import joblib
    except ImportError:
        logger.error("[PERSIST] joblib nao instalado")
        return False

    paths = get_paths()
    safe_source = _safe_source_name(source)
    model_path = paths["models_dir"] / f"{safe_source}.pkl"
    tmp_path = paths["models_dir"] / f"{safe_source}.pkl.tmp"

    payload = {
        "model": model,
        "scaler": scaler,
        "source": source,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        joblib.dump(payload, tmp_path)
        tmp_path.replace(model_path)
        logger.info(f"[PERSIST] Modelo salvo: {model_path.name}")
        return True
    except Exception as e:
        logger.error(f"[PERSIST] Erro salvando modelo {source}: {e}")
        return False


def load_model(source: str) -> Optional[dict]:
    """
    Carrega modelo. Retorna dict {model, scaler, source, saved_at} ou None.
    """
    try:
        import joblib
    except ImportError:
        logger.error("[PERSIST] joblib nao instalado")
        return None

    paths = get_paths()
    safe_source = _safe_source_name(source)
    model_path = paths["models_dir"] / f"{safe_source}.pkl"

    if not model_path.exists():
        return None

    try:
        return joblib.load(model_path)
    except Exception as e:
        logger.error(f"[PERSIST] Erro carregando modelo {source}: {e}")
        return None


def load_all_models() -> dict[str, dict]:
    """Carrega todos modelos disponiveis. Retorna {source: payload}."""
    paths = get_paths()
    out = {}

    if not paths["models_dir"].exists():
        return out

    for model_path in paths["models_dir"].glob("*.pkl"):
        source = model_path.stem
        payload = load_model(source)
        if payload:
            actual_source = payload.get("source", source)
            out[actual_source] = payload

    return out


def save_metadata(metadata: dict) -> bool:
    """Salva metadata.json com metricas de cada modelo."""
    paths = get_paths()
    tmp = paths["metadata"].with_suffix(".json.tmp")

    metadata["last_modified"] = datetime.now(timezone.utc).isoformat()

    try:
        with open(tmp, "w") as f:
            json.dump(metadata, f, indent=2, default=str)
        tmp.replace(paths["metadata"])
        return True
    except Exception as e:
        logger.error(f"[PERSIST] Erro salvando metadata: {e}")
        return False


def load_metadata() -> dict:
    """Carrega metadata. Retorna dict vazio se nao existir."""
    paths = get_paths()
    if not paths["metadata"].exists():
        return {"version": "1.0.0", "models": {}}
    try:
        with open(paths["metadata"]) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[PERSIST] Erro carregando metadata: {e}")
        return {"version": "1.0.0", "models": {}}


def log_observation(observation: dict) -> None:
    """Append em observations.jsonl (modo observacao)."""
    paths = get_paths()
    try:
        with open(paths["observations"], "a") as f:
            f.write(json.dumps(observation, default=str) + "\n")
    except Exception as e:
        logger.error(f"[PERSIST] Erro logando observation: {e}")


def log_retrain(entry: dict) -> None:
    """Append em retrain_log.jsonl."""
    paths = get_paths()
    try:
        with open(paths["retrain_log"], "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        logger.error(f"[PERSIST] Erro logando retrain: {e}")


def get_recent_observations(days: int = 7) -> list[dict]:
    """Le observations dos ultimos N dias."""
    from datetime import timedelta
    paths = get_paths()

    if not paths["observations"].exists():
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    obs = []
    try:
        with open(paths["observations"]) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                    if o.get("timestamp", "") >= cutoff:
                        obs.append(o)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.error(f"[PERSIST] Erro lendo observations: {e}")

    return obs
