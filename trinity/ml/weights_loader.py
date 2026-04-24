"""
WeightsLoader — carrega pesos otimizados de ml_weight_optimizer.json e os
fornece aos scoring engines. Cache TTL 5min.

Fail-safe: qualquer erro (arquivo ausente, shape inesperado, recommend=False,
parse error) -> retorna None -> chamador usa pesos LEGADOS (comportamento atual).
Sistema NUNCA quebra por causa do loader.

IMPORTANTE: o path aqui bate EXATAMENTE com o que weight_optimizer.py usa em
RESULTS_FILE: pathlib.Path(__file__).parent.parent.parent / "dashboard" /
"ml_weight_optimizer.json" (sem .resolve()).
"""

import json
import logging
import pathlib
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Mesmo pattern do weight_optimizer.py — sem .resolve() pra evitar divergencia
_WEIGHTS_FILE = pathlib.Path(__file__).parent.parent.parent / "dashboard" / "ml_weight_optimizer.json"

_CACHE_TTL = 300  # 5min
_cache: Dict[str, tuple] = {}  # direction -> (ts, weights_dict_or_None)


def get_weights(direction: str) -> Optional[Dict[str, float]]:
    """
    Retorna dict {detector_name: weight} ou None se nao disponivel.
    Quando None, chamador deve usar pesos LEGADOS.
    """
    direction = direction.upper()
    now = time.time()
    entry = _cache.get(direction)
    if entry and (now - entry[0]) < _CACHE_TTL:
        return entry[1]

    weights = _load_from_disk(direction)
    _cache[direction] = (now, weights)
    return weights


def _load_from_disk(direction: str) -> Optional[Dict[str, float]]:
    if not _WEIGHTS_FILE.exists():
        return None
    try:
        data = json.loads(_WEIGHTS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[WeightsLoader] falha ao ler {_WEIGHTS_FILE}: {e}")
        return None

    # Shape esperado (conforme weight_optimizer.py:548-559):
    #   {"LONG": {"status":"ok", "recommend":bool, "weights":{name:float,...}, ...},
    #    "SHORT": {...}}
    section = data.get(direction) or data.get(direction.lower())
    if not isinstance(section, dict):
        return None
    if not section.get("recommend", False):
        logger.info(f"[WeightsLoader] {direction}: recommend=False, usando pesos legacy")
        return None
    w = section.get("weights")
    if not isinstance(w, dict) or not w:
        return None

    result = {}
    for k, v in w.items():
        try:
            result[str(k)] = float(v)
        except (TypeError, ValueError):
            logger.warning(f"[WeightsLoader] {direction}: peso invalido para {k}={v}, ignorando")
    return result or None


def stats() -> dict:
    return {
        "weights_file": str(_WEIGHTS_FILE),
        "weights_file_exists": _WEIGHTS_FILE.exists(),
        "cache": {
            direction: {
                "age_s": int(time.time() - ts),
                "has_weights": w is not None,
                "weights_keys": list(w.keys()) if w else [],
            }
            for direction, (ts, w) in _cache.items()
        },
    }
