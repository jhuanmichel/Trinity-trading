"""
Feature Extractor

Converte um outcome dict em vetor de features para o RF.

Principios:
- Deterministico: mesmo outcome -> mesmo vetor (sem aleatoriedade)
- Defensivo: campo ausente -> 0.0 (nao quebra)
- Versionado: schema persistido em feature_columns.json
- Pure: sem state, sem side effects

Adaptado aos outcomes REAIS do Trinity (vide ML_PIPELINE_DIAGNOSTIC):
- layer_scores variam por source (pump: silent_acc/squeeze/gravity/breakout,
  crash: cascade/collapse/whale/volatility, funding: level/fuel/context/volume)
- Usamos SUPERSET de todos layer_scores conhecidos (missing = 0.0)
- timestamp pode estar em "timestamp" ou "registered_at"
"""

from __future__ import annotations
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("rf_classifier.feature_extractor")


# ============================================================
# FEATURES — adaptadas aos outcomes reais do Trinity
# ============================================================

# Score composto + layer_scores (superset de todas as sources)
NUMERIC_FEATURES = [
    "score",
    # Layer scores: pump_trader (4)
    "ls_silent_acc",
    "ls_squeeze",
    "ls_gravity",
    "ls_breakout",
    # Layer scores: crash_trader (4)
    "ls_cascade",
    "ls_collapse",
    "ls_whale",
    "ls_volatility",
    # Layer scores: funding_scanner (4)
    "ls_level",
    "ls_fuel",
    "ls_context",
    "ls_volume",
]

# Booleanas convertidas pra 0/1
BOOLEAN_FEATURES = [
    "is_blue_chip",
]

# Categoricas (one-hot)
CATEGORICAL_FEATURES = {
    "btc_regime": ["STRONG_BEAR", "BEAR", "NEUTRAL", "BULL", "STRONG_BULL", "UNKNOWN"],
    "direction": ["LONG", "SHORT"],
    "conviction_tier": ["EXTREME", "STRONG", "TRADEABLE", "WEAK", "MICRO", "UNKNOWN"],
}

# Temporais (do timestamp do outcome)
TEMPORAL_FEATURES = [
    "hour_sin",
    "hour_cos",
    "day_of_week",
    "is_weekend",
    "minutes_to_round_hour",
]


def _safe_float(value, default: float = 0.0) -> float:
    """Converte qualquer coisa pra float com fallback."""
    if value is None:
        return default
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_bool(value) -> int:
    """Converte qualquer coisa pra 0/1."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value > 0 else 0
    if isinstance(value, str):
        return 1 if value.lower() in ("true", "1", "yes", "on") else 0
    return 0


def _get_timestamp(outcome: dict) -> str:
    """Extrai timestamp (suporta 'timestamp' OU 'registered_at')."""
    return outcome.get("timestamp") or outcome.get("registered_at") or ""


def _temporal_features(timestamp: str) -> dict:
    """Extrai features temporais do timestamp ISO."""
    out = {f: 0.0 for f in TEMPORAL_FEATURES}
    if not timestamp:
        return out

    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except Exception:
        return out

    hour = dt.hour
    minute = dt.minute

    out["hour_sin"] = math.sin(2 * math.pi * hour / 24)
    out["hour_cos"] = math.cos(2 * math.pi * hour / 24)
    out["day_of_week"] = float(dt.weekday())
    out["is_weekend"] = 1.0 if dt.weekday() >= 5 else 0.0
    out["minutes_to_round_hour"] = float(min(minute, 60 - minute))

    return out


# Map: layer_scores key -> feature name
_LAYER_SCORE_MAP = {
    "silent_acc": "ls_silent_acc",
    "squeeze":    "ls_squeeze",
    "gravity":    "ls_gravity",
    "breakout":   "ls_breakout",
    "cascade":    "ls_cascade",
    "collapse":   "ls_collapse",
    "whale":      "ls_whale",
    "volatility": "ls_volatility",
    "level":      "ls_level",
    "fuel":       "ls_fuel",
    "context":    "ls_context",
    "volume":     "ls_volume",
}


def _flatten_layer_scores(outcome: dict) -> dict:
    """
    Achata layer_scores em features individuais.
    Suporta tanto outcome.layer_scores={"silent_acc": X} (dict)
    quanto outcome diretamente com chaves component_scores={...}.
    """
    out = {f: 0.0 for f in NUMERIC_FEATURES if f.startswith("ls_")}

    for source_field in ("layer_scores", "component_scores"):
        ls = outcome.get(source_field)
        if isinstance(ls, dict):
            for k, v in ls.items():
                feat_name = _LAYER_SCORE_MAP.get(k)
                if feat_name:
                    out[feat_name] = _safe_float(v)
            break  # primeiro encontrado vence
    return out


def get_feature_columns() -> list[str]:
    """
    Retorna lista ordenada de TODAS as features que o RF vai receber.
    Esta ordem e IMUTAVEL — nao pode mudar entre treino e inferencia.
    """
    cols = []
    cols.extend(NUMERIC_FEATURES)
    cols.extend(BOOLEAN_FEATURES)

    for cat_name, values in CATEGORICAL_FEATURES.items():
        for val in values:
            cols.append(f"{cat_name}__{val}")

    cols.extend(TEMPORAL_FEATURES)
    return cols


def extract_features(outcome: dict) -> dict:
    """
    Extrai features de um outcome.

    Retorna dict {feature_name: float}.
    Toda feature ausente vira 0.0 (defensivo).
    """
    features = {}

    # score (numero direto)
    features["score"] = _safe_float(outcome.get("score"))

    # layer scores (achatados)
    features.update(_flatten_layer_scores(outcome))

    # Booleanas
    for f in BOOLEAN_FEATURES:
        features[f] = float(_safe_bool(outcome.get(f)))

    # One-hot categoricas
    for cat_name, values in CATEGORICAL_FEATURES.items():
        outcome_val = str(outcome.get(cat_name, "")).upper()
        for val in values:
            features[f"{cat_name}__{val}"] = 1.0 if outcome_val == val else 0.0

    # Temporais
    timestamp = _get_timestamp(outcome)
    features.update(_temporal_features(timestamp))

    return features


def extract_features_vector(outcome: dict, feature_columns: Optional[list[str]] = None) -> list[float]:
    """
    Extrai features como VETOR ORDENADO (pro sklearn).
    """
    if feature_columns is None:
        feature_columns = get_feature_columns()

    features = extract_features(outcome)
    return [features.get(col, 0.0) for col in feature_columns]


def save_feature_schema(path: Path) -> None:
    """Salva schema das features (pra audit + reload)."""
    schema = {
        "version": "1.0.0",
        "feature_columns": get_feature_columns(),
        "numeric_features": NUMERIC_FEATURES,
        "boolean_features": BOOLEAN_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "temporal_features": TEMPORAL_FEATURES,
        "layer_score_map": _LAYER_SCORE_MAP,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(schema, f, indent=2)


def load_feature_schema(path: Path) -> Optional[dict]:
    """Carrega schema salvo. Retorna None se nao existir."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[FEAT] Erro carregando schema: {e}")
        return None
