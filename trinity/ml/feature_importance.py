"""
trinity/ml/feature_importance.py
Analisa quais detectors (layer_scores) realmente predizem WIN vs LOSS.

Formato real dos outcomes (confirmado no outcome_tracker.py):
  - layer_scores: {silent_acc, squeeze, gravity, breakout}  (LONG)
                  {cascade, collapse, whale, volatility}     (SHORT)
  - status: "WIN" | "LOSS" | "NEUTRAL"
  - score: float (score composto 0-100)
  - direction: "LONG" | "SHORT"
  - conviction_tier: str
  - Files: logs/outcomes_*.jsonl (glob mensal)

Métricas calculadas (stdlib only, zero dependências externas):
  - Ponto-bisserial: correlação de cada feature com WIN=1/LOSS=0
  - Cohen's d: tamanho do efeito (|mean_win - mean_loss| / std_pooled)
  - Win rate por quartil: onde cada detector performa melhor
  - Separação normalizada: diferença de médias como % do range
"""

from __future__ import annotations

import json
import math
import pathlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Mínimo de outcomes resolvidos para análise significativa
MIN_OUTCOMES = 30

# Diretório de logs (relativo à raiz do projeto)
LOGS_DIR = pathlib.Path(__file__).parent.parent.parent / "logs"

# Features que sempre extraímos se disponíveis
KNOWN_FEATURES = [
    "silent_acc", "squeeze", "gravity", "breakout",   # LONG / pump
    "cascade",   "collapse", "whale",   "volatility", # SHORT / crash
    "score",                                           # score composto
]


# ---------------------------------------------------------------------------
# Carregamento de dados
# ---------------------------------------------------------------------------

def _load_resolved_outcomes() -> list[dict]:
    """Carrega todos os outcomes resolvidos de logs/outcomes_*.jsonl."""
    outcomes: list[dict] = []
    files = sorted(LOGS_DIR.glob("outcomes_*.jsonl"))
    if not files:
        logger.warning("[ML] Nenhum arquivo outcomes_*.jsonl encontrado em %s", LOGS_DIR)
        return outcomes

    for fpath in files:
        try:
            for line in fpath.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    outcomes.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.debug("[ML] Linha inválida em %s: %s", fpath.name, e)
        except Exception as e:
            logger.warning("[ML] Erro ao ler %s: %s", fpath.name, e)

    logger.info("[ML] %d outcomes carregados de %d arquivo(s)", len(outcomes), len(files))
    return outcomes


def _extract_features(outcome: dict) -> dict[str, float] | None:
    """Extrai features numéricas de um outcome. Retorna None se inválido."""
    status = outcome.get("status", "")
    if status not in ("WIN", "LOSS"):
        return None  # Ignora NEUTRAL / EXPIRED

    label = 1 if status == "WIN" else 0

    layer_scores: dict = outcome.get("layer_scores") or {}
    score: float = float(outcome.get("score", 0.0))

    features: dict[str, float] = {"_label": float(label), "score": score}

    for key in KNOWN_FEATURES:
        if key == "score":
            continue
        if key in layer_scores:
            try:
                features[key] = float(layer_scores[key])
            except (TypeError, ValueError):
                pass

    # Precisa de pelo menos 1 detector além do score
    if len(features) < 3:
        return None

    return features


# ---------------------------------------------------------------------------
# Matemática estatística (stdlib puro)
# ---------------------------------------------------------------------------

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def _std(values: list[float]) -> float:
    return math.sqrt(_variance(values))


def _pearson_pointbiserial(binary: list[float], continuous: list[float]) -> float:
    """
    Correlação ponto-bisserial: equivalente à correlação de Pearson quando
    uma variável é binária (0/1).
    """
    n = len(binary)
    if n < 4:
        return 0.0

    n1 = sum(binary)         # qtd de 1s
    n0 = n - n1              # qtd de 0s
    if n1 == 0 or n0 == 0:
        return 0.0

    # Média de continuous onde binary=1 e onde binary=0
    m1 = _mean([c for b, c in zip(binary, continuous) if b == 1])
    m0 = _mean([c for b, c in zip(binary, continuous) if b == 0])
    sd = _std(continuous)

    if sd == 0:
        return 0.0

    return (m1 - m0) / sd * math.sqrt(n1 * n0 / (n * n))


def _cohen_d(group1: list[float], group0: list[float]) -> float:
    """Cohen's d: tamanho do efeito entre dois grupos."""
    if not group1 or not group0:
        return 0.0
    m1, m0 = _mean(group1), _mean(group0)
    v1, v0 = _variance(group1), _variance(group0)
    n1, n0 = len(group1), len(group0)
    pooled_std = math.sqrt((v1 * (n1 - 1) + v0 * (n0 - 1)) / (n1 + n0 - 2))
    if pooled_std == 0:
        return 0.0
    return (m1 - m0) / pooled_std


def _quartile_win_rates(labels: list[float], values: list[float]) -> list[dict]:
    """Win rate por quartil de uma feature."""
    if len(values) < 8:
        return []

    paired = sorted(zip(values, labels))
    q = len(paired) // 4
    quartiles = []
    labels_q = ["Q1 (baixo)", "Q2", "Q3", "Q4 (alto)"]

    for i, lbl in enumerate(labels_q):
        if i < 3:
            chunk = paired[i * q: (i + 1) * q]
        else:
            chunk = paired[i * q:]

        wins = sum(1 for _, l in chunk if l == 1)
        total = len(chunk)
        wr = wins / total if total > 0 else 0.0
        quartiles.append({
            "quartil": lbl,
            "win_rate": round(wr * 100, 1),
            "n": total,
        })

    return quartiles


# ---------------------------------------------------------------------------
# Classe principal
# ---------------------------------------------------------------------------

class FeatureImportanceAnalyzer:
    """
    Analisa correlação de cada detector (layer_scores) com o resultado WIN/LOSS.
    Lê todos os outcomes_*.jsonl de LOGS_DIR. Totalmente read-only.
    """

    def __init__(self) -> None:
        self._last_result: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    def analyze(self) -> dict[str, Any]:
        """
        Roda a análise e retorna um dict com:
          status: "ok" | "insufficient_data" | "no_data"
          n_total / n_wins / n_losses
          features: lista ordenada por |cohen_d|
          direction_breakdown: por LONG/SHORT
          timestamp: ISO
        """
        import datetime

        raw = _load_resolved_outcomes()
        resolved = [o for o in raw if o.get("status") in ("WIN", "LOSS")]
        n_wins   = sum(1 for o in resolved if o["status"] == "WIN")
        n_losses = sum(1 for o in resolved if o["status"] == "LOSS")

        result: dict[str, Any] = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "n_total":   len(resolved),
            "n_wins":    n_wins,
            "n_losses":  n_losses,
        }

        if len(resolved) < MIN_OUTCOMES:
            result["status"] = "insufficient_data"
            result["min_required"] = MIN_OUTCOMES
            result["features"] = []
            result["direction_breakdown"] = {}
            self._last_result = result
            logger.info("[ML] Dados insuficientes: %d / %d requeridos", len(resolved), MIN_OUTCOMES)
            return result

        # Extrai features de todos os outcomes válidos
        rows: list[dict] = []
        for o in resolved:
            f = _extract_features(o)
            if f is not None:
                f["_direction"] = o.get("direction", "UNKNOWN")
                rows.append(f)

        if not rows:
            result["status"] = "no_data"
            result["features"] = []
            result["direction_breakdown"] = {}
            self._last_result = result
            return result

        # Identifica features disponíveis (excluindo label e direção)
        feature_names = sorted({k for r in rows for k in r if not k.startswith("_")})

        labels = [r["_label"] for r in rows]
        feature_stats: list[dict] = []

        for feat in feature_names:
            vals = [r[feat] for r in rows if feat in r]
            lbls = [r["_label"] for r in rows if feat in r]

            if len(vals) < MIN_OUTCOMES:
                continue

            wins_vals  = [v for v, l in zip(vals, lbls) if l == 1]
            losses_vals = [v for v, l in zip(vals, lbls) if l == 0]

            pb   = _pearson_pointbiserial(lbls, vals)
            cd   = _cohen_d(wins_vals, losses_vals)
            mw   = _mean(wins_vals)
            ml   = _mean(losses_vals)
            vmin = min(vals)
            vmax = max(vals)
            sep  = (mw - ml) / (vmax - vmin) * 100 if (vmax - vmin) > 0 else 0.0

            feature_stats.append({
                "feature":          feat,
                "point_biserial":   round(pb, 4),
                "cohen_d":          round(cd, 4),
                "mean_wins":        round(mw, 3),
                "mean_losses":      round(ml, 3),
                "separation_pct":   round(sep, 2),
                "n_wins":           len(wins_vals),
                "n_losses":         len(losses_vals),
                "quartile_win_rates": _quartile_win_rates(lbls, vals),
            })

        # Ordena por |cohen_d| decrescente (maior efeito primeiro)
        feature_stats.sort(key=lambda x: abs(x["cohen_d"]), reverse=True)

        # Breakdown por direção (LONG / SHORT)
        direction_breakdown: dict[str, dict] = {}
        for direction in ("LONG", "SHORT"):
            dir_rows = [r for r in rows if r.get("_direction") == direction]
            if len(dir_rows) < 10:
                continue
            dir_lbls = [r["_label"] for r in dir_rows]
            dir_wins  = sum(dir_lbls)
            dir_total = len(dir_lbls)
            dir_stats = []
            for feat in feature_names:
                dir_vals = [r[feat] for r in dir_rows if feat in r]
                d_lbls   = [r["_label"] for r in dir_rows if feat in r]
                if len(dir_vals) < 8:
                    continue
                w_vals = [v for v, l in zip(dir_vals, d_lbls) if l == 1]
                l_vals = [v for v, l in zip(dir_vals, d_lbls) if l == 0]
                cd_dir = _cohen_d(w_vals, l_vals)
                dir_stats.append({
                    "feature": feat,
                    "cohen_d": round(cd_dir, 4),
                    "mean_wins": round(_mean(w_vals), 3),
                    "mean_losses": round(_mean(l_vals), 3),
                })
            dir_stats.sort(key=lambda x: abs(x["cohen_d"]), reverse=True)
            direction_breakdown[direction] = {
                "n_total": dir_total,
                "n_wins":  int(dir_wins),
                "win_rate_pct": round(dir_wins / dir_total * 100, 1) if dir_total else 0,
                "features": dir_stats,
            }

        result["status"] = "ok"
        result["features"] = feature_stats
        result["direction_breakdown"] = direction_breakdown
        self._last_result = result
        logger.info("[ML] Feature importance concluída: %d features, %d trades", len(feature_stats), len(rows))
        return result

    # ------------------------------------------------------------------
    def last_result(self) -> dict[str, Any] | None:
        return self._last_result

    # ------------------------------------------------------------------
    def top_predictors(self, n: int = 3) -> list[str]:
        """Retorna nomes das n features mais preditivas (por |cohen_d|)."""
        if not self._last_result or not self._last_result.get("features"):
            return []
        return [f["feature"] for f in self._last_result["features"][:n]]
