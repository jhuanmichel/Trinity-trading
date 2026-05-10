"""
Inference - predict prob_win em runtime.

Otimizado pra ser RAPIDO (chamado em cada sinal candidato).
Modelos carregados em cache (1x por processo).
Fail-open em qualquer erro.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

from trinity.rf_classifier.feature_extractor import (
    extract_features_vector,
    get_feature_columns,
)
from trinity.rf_classifier.persistence import load_model, load_metadata

logger = logging.getLogger("rf_classifier.inference")


# Cache em memoria (carregado 1x por processo)
_model_cache: dict[str, Optional[dict]] = {}
_metadata_cache: Optional[dict] = None
_cache_loaded_at: Optional[datetime] = None


def _ensure_cache_fresh() -> None:
    """
    Recarrega cache se modelo no disco for mais novo que o cached.
    Por enquanto: carrega 1x e pronto. Reload acontece em retrain -> reset_cache.
    """
    global _model_cache, _metadata_cache, _cache_loaded_at

    if _cache_loaded_at is not None:
        return

    _metadata_cache = load_metadata()
    _cache_loaded_at = datetime.now(timezone.utc)

    # Pre-carregar todos modelos do metadata
    for source in _metadata_cache.get("models", {}).keys():
        try:
            payload = load_model(source)
            _model_cache[source] = payload
            if payload:
                logger.info(f"[INFER] Modelo {source} carregado em cache")
        except Exception as e:
            logger.warning(f"[INFER] Erro carregando {source}: {e}")
            _model_cache[source] = None


def predict_prob_win(
    source: str,
    outcome_features: dict,
) -> Optional[float]:
    """
    Prediz probabilidade de WIN pra um candidato.

    Args:
        source: nome da source (pra escolher modelo certo)
        outcome_features: dict similar ao outcome (features pra extrair)

    Returns:
        prob_win em [0, 1], ou None se modelo nao disponivel.

    Fail-open: qualquer erro retorna None (caller deve passar o sinal).
    """
    try:
        _ensure_cache_fresh()

        if source not in _model_cache or _model_cache[source] is None:
            return None

        model_payload = _model_cache[source]
        model = model_payload.get("model")
        if model is None:
            return None

        feature_columns = get_feature_columns()
        x = extract_features_vector(outcome_features, feature_columns)

        try:
            proba = model.predict_proba([x])[0]
            if len(proba) >= 2:
                return float(proba[1])
            else:
                return 0.5
        except Exception as e:
            logger.debug(f"[INFER] predict_proba falhou ({source}): {e}")
            return None

    except Exception as e:
        logger.debug(f"[INFER] erro inesperado: {e}")
        return None


def get_model_info(source: str) -> Optional[dict]:
    """Retorna info do modelo (do metadata)."""
    _ensure_cache_fresh()
    if not _metadata_cache:
        return None
    return _metadata_cache.get("models", {}).get(source)


def list_available_models() -> list[str]:
    """Lista sources que tem modelo treinado."""
    _ensure_cache_fresh()
    if not _metadata_cache:
        return []
    return list(_metadata_cache.get("models", {}).keys())


def reset_cache() -> None:
    """Forca reload do cache (uso apos retrain)."""
    global _model_cache, _metadata_cache, _cache_loaded_at
    _model_cache = {}
    _metadata_cache = None
    _cache_loaded_at = None
    logger.info("[INFER] Cache resetado - recarrega no proximo predict")
