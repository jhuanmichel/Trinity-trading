"""
Trainer - treina 1 RF por source.

Pipeline:
1. Carrega outcomes resolvidos
2. Filtra por source
3. Verifica volume minimo (>= 200) e balance
4. Extrai features
5. Walk-forward validation
6. SE estavel -> treina modelo final em TODOS os dados
7. Salva modelo + metadata
"""

from __future__ import annotations
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from trinity.rf_classifier import (
    MIN_SAMPLES_PER_SOURCE,
    MIN_BALANCE_RATIO,
    WALK_FORWARD_FOLDS,
)
from trinity.rf_classifier.feature_extractor import (
    extract_features_vector,
    get_feature_columns,
    save_feature_schema,
)
from trinity.rf_classifier.persistence import (
    save_model,
    save_metadata,
    load_metadata,
    log_retrain,
    get_paths,
)
from trinity.rf_classifier.walk_forward import walk_forward_validate

logger = logging.getLogger("rf_classifier.trainer")


def _load_resolved_outcomes() -> list[dict]:
    """Carrega TODOS outcomes WIN/LOSS dos arquivos jsonl."""
    outcomes = []

    for d in [Path("/data/logs"), Path("logs")]:
        if not d.exists():
            continue
        for f in d.glob("outcomes_*.jsonl"):
            try:
                with open(f) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            o = json.loads(line)
                            status = str(o.get("status", "")).upper()
                            if status in ("WIN", "LOSS"):
                                outcomes.append(o)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning(f"[TRAIN] Erro lendo {f}: {e}")

    # Ordenar por timestamp (suporta timestamp OU registered_at)
    outcomes.sort(key=lambda o: o.get("timestamp") or o.get("registered_at") or "")
    return outcomes


def _outcome_to_label(outcome: dict) -> int:
    """Outcome -> 0/1."""
    status = str(outcome.get("status", "")).upper()
    return 1 if status == "WIN" else 0


def _outcome_timestamp(outcome: dict) -> str:
    """Pega timestamp do outcome (timestamp OU registered_at)."""
    return outcome.get("timestamp") or outcome.get("registered_at") or ""


def _check_balance(y: list[int]) -> dict:
    """Verifica balanceamento da classe."""
    wins = sum(y)
    losses = len(y) - wins

    if wins == 0 or losses == 0:
        return {"valid": False, "reason": "single_class", "wins": wins, "losses": losses}

    ratio = min(wins, losses) / max(wins, losses)

    return {
        "valid": True,
        "wins": wins,
        "losses": losses,
        "ratio": ratio,
        "use_class_weight": ratio < MIN_BALANCE_RATIO,
    }


def train_source(source: str, outcomes: list[dict]) -> dict:
    """
    Treina 1 RF pra 1 source.
    """
    result = {
        "source": source,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "samples": len(outcomes),
        "status": "unknown",
    }

    if len(outcomes) < MIN_SAMPLES_PER_SOURCE:
        result["status"] = "skipped"
        result["reason"] = f"insufficient_samples: {len(outcomes)} < {MIN_SAMPLES_PER_SOURCE}"
        logger.info(f"[TRAIN] {source}: SKIP ({result['reason']})")
        return result

    try:
        from sklearn.ensemble import RandomForestClassifier
    except ImportError as e:
        result["status"] = "error"
        result["reason"] = f"sklearn_import: {e}"
        return result

    feature_columns = get_feature_columns()
    X = []
    y = []
    timestamps = []

    for o in outcomes:
        try:
            X.append(extract_features_vector(o, feature_columns))
            y.append(_outcome_to_label(o))
            timestamps.append(_outcome_timestamp(o))
        except Exception as e:
            logger.warning(f"[TRAIN] {source}: erro extraindo features: {e}")
            continue

    result["samples_with_features"] = len(X)

    if len(X) < MIN_SAMPLES_PER_SOURCE:
        result["status"] = "skipped"
        result["reason"] = f"insufficient_after_feature_extract: {len(X)}"
        return result

    balance = _check_balance(y)
    result["balance"] = balance

    if not balance["valid"]:
        result["status"] = "skipped"
        result["reason"] = f"unbalanced: {balance['reason']}"
        return result

    class_weight = "balanced" if balance["use_class_weight"] else None

    def model_factory():
        return RandomForestClassifier(
            n_estimators=100,
            max_depth=8,
            min_samples_leaf=10,
            class_weight=class_weight,
            random_state=42,
            n_jobs=2,  # cuidado com CPU em 1-vCPU instance
        )

    logger.info(f"[TRAIN] {source}: walk-forward com {len(X)} samples")
    wf_result = walk_forward_validate(
        X=X,
        y=y,
        timestamps=timestamps,
        model_factory=model_factory,
        n_folds=WALK_FORWARD_FOLDS,
        min_train_size=max(MIN_SAMPLES_PER_SOURCE, len(X) // (WALK_FORWARD_FOLDS + 1)),
    )

    result["walk_forward"] = wf_result

    if "error" in wf_result:
        result["status"] = "skipped"
        result["reason"] = f"walk_forward_error: {wf_result['error']}"
        return result

    if not wf_result.get("recommend_apply", False):
        result["status"] = "trained_but_not_recommended"
        result["reason"] = (
            f"AUC mean {wf_result['auc_mean']:.3f}, "
            f"std {wf_result['auc_std']:.3f}, "
            f"stable={wf_result['is_stable']}"
        )
        logger.info(f"[TRAIN] {source}: {result['reason']}")
        # Treina mesmo assim mas marca como nao recomendado

    try:
        final_model = model_factory()
        final_model.fit(X, y)

        importances = {}
        if hasattr(final_model, "feature_importances_"):
            for col, imp in zip(feature_columns, final_model.feature_importances_):
                importances[col] = float(imp)

        result["feature_importances"] = importances
        result["top_features"] = sorted(
            importances.items(),
            key=lambda x: -x[1]
        )[:10]

        saved = save_model(source, final_model, scaler=None)
        result["saved"] = saved
        if result["status"] == "unknown":
            result["status"] = "success" if saved else "save_failed"

        logger.info(
            f"[TRAIN] {source}: OK AUC={wf_result['auc_mean']:.3f}+-{wf_result['auc_std']:.3f}"
        )

    except Exception as e:
        logger.exception(f"[TRAIN] {source}: erro treinando final")
        result["status"] = "error"
        result["reason"] = f"final_train_error: {e}"

    return result


def train_all_sources() -> dict:
    """
    Treina RFs pra todas sources com volume suficiente.
    """
    result = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "sources": {},
    }

    outcomes = _load_resolved_outcomes()
    result["total_outcomes"] = len(outcomes)

    if not outcomes:
        result["status"] = "no_outcomes"
        return result

    paths = get_paths()
    save_feature_schema(paths["feature_columns"])

    by_source = defaultdict(list)
    for o in outcomes:
        src = o.get("source", "unknown")
        by_source[src].append(o)

    for source, source_outcomes in by_source.items():
        try:
            source_result = train_source(source, source_outcomes)
            result["sources"][source] = source_result
        except Exception as e:
            logger.exception(f"[TRAIN] {source}: exception")
            result["sources"][source] = {
                "status": "exception",
                "error": str(e),
            }

    metadata = load_metadata()
    metadata["last_trained_at"] = result["executed_at"]
    metadata["total_outcomes"] = result["total_outcomes"]

    if "models" not in metadata:
        metadata["models"] = {}

    for source, source_result in result["sources"].items():
        if source_result.get("status") in ("success", "trained_but_not_recommended"):
            wf = source_result.get("walk_forward", {})
            metadata["models"][source] = {
                "samples": source_result.get("samples_with_features", 0),
                "wins": source_result.get("balance", {}).get("wins", 0),
                "losses": source_result.get("balance", {}).get("losses", 0),
                "auc_mean": wf.get("auc_mean"),
                "auc_std": wf.get("auc_std"),
                "is_stable": wf.get("is_stable"),
                "recommend_apply": wf.get("recommend_apply"),
                "trained_at": source_result.get("trained_at"),
                "top_features": source_result.get("top_features", [])[:5],
            }

    save_metadata(metadata)

    log_retrain({
        "timestamp": result["executed_at"],
        "total_outcomes": result["total_outcomes"],
        "sources_trained": [
            s for s, r in result["sources"].items()
            if r.get("status") in ("success", "trained_but_not_recommended")
        ],
        "sources_skipped": [
            s for s, r in result["sources"].items()
            if r.get("status") == "skipped"
        ],
    })

    result["status"] = "completed"
    return result
