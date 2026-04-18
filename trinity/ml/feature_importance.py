"""
trinity/ml/feature_importance.py — v2
FeatureImportanceAnalyzer: 5 métricas, análise multidimensional.

Formato real dos outcomes (confirmado no outcome_tracker.py):
  layer_scores: {silent_acc, squeeze, gravity, breakout}  (LONG/pump)
                {cascade, collapse, whale, volatility}     (SHORT/crash)
  status:          "WIN" | "LOSS" | "NEUTRAL"
  score:           float 0-100 (score composto)
  direction:       "LONG" | "SHORT"
  conviction_tier: str
  resolved_at:     ISO 8601
  Files:           logs/outcomes_*.jsonl (glob mensal)

Métricas calculadas (stdlib puro, zero dependências externas):
  1. Point-Biserial Correlation (r_pb)  — correlação binária/contínua
  2. Cohen's D                          — tamanho do efeito padronizado
  3. AUC (Mann-Whitney U)               — P(score_win > score_loss)
  4. Information Value (WoE, 10 bins)   — poder preditivo de crédito
  5. Composite Score                    — média ponderada normalizada

Análise multidimensional:
  - Global (todos os trades)
  - Por source:          LONG | SHORT
  - Por conviction_tier: EXTREME | HIGH | MEDIUM | LOW
  - Por score_band:      0-25 | 25-50 | 50-75 | 75-100
"""

from __future__ import annotations

import json
import math
import pathlib
import logging
import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Mínimo de outcomes WIN+LOSS para análise significativa
MIN_OUTCOMES = 30

# Mínimo por subgrupo (source, tier, band)
MIN_SUBGROUP = 10

# Diretório de logs (relativo à raiz do projeto)
LOGS_DIR = pathlib.Path(__file__).parent.parent.parent / "logs"

# Arquivo de persistência dos resultados
RESULTS_FILE = pathlib.Path(__file__).parent.parent.parent / "dashboard" / "ml_feature_importance.json"

# Detectores conhecidos por source
LONG_FEATURES  = ["silent_acc", "squeeze", "gravity", "breakout"]
SHORT_FEATURES = ["cascade", "collapse", "whale", "volatility"]
ALL_FEATURES   = LONG_FEATURES + SHORT_FEATURES + ["score"]

# Interpretação do composite score
COMPOSITE_LABELS = {
    0.60: "forte",
    0.40: "moderado",
    0.20: "fraco",
    0.0:  "negligível",
}

# Score bands para análise por faixa de score composto
SCORE_BANDS = [
    ("0-25",  0,   25),
    ("25-50", 25,  50),
    ("50-75", 50,  75),
    ("75-100",75, 100),
]

# Pesos no composite score
_W_RPB = 0.25
_W_D   = 0.35
_W_AUC = 0.25
_W_IV  = 0.15


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _load_resolved_outcomes() -> list[dict]:
    """Carrega todos os outcomes resolvidos de logs/outcomes_*.jsonl."""
    outcomes: list[dict] = []
    files = sorted(LOGS_DIR.glob("outcomes_*.jsonl"))
    if not files:
        logger.warning("[FI] Nenhum arquivo outcomes_*.jsonl em %s", LOGS_DIR)
        return outcomes

    for fpath in files:
        try:
            for line in fpath.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    outcomes.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.debug("[FI] Linha inválida em %s: %s", fpath.name, exc)
        except Exception as exc:
            logger.warning("[FI] Erro ao ler %s: %s", fpath.name, exc)

    logger.info("[FI] %d outcomes carregados de %d arquivo(s)", len(outcomes), len(files))
    return outcomes


def _extract_row(outcome: dict) -> dict | None:
    """
    Extrai um row de features de um outcome resolvido.
    Retorna None se status não for WIN/LOSS ou se não houver layer_scores úteis.
    """
    status = outcome.get("status", "")
    if status not in ("WIN", "LOSS"):
        return None

    layer: dict = outcome.get("layer_scores") or {}
    score = float(outcome.get("score", 0.0))

    row: dict[str, Any] = {
        "_label":     1 if status == "WIN" else 0,
        "_direction": outcome.get("direction", "UNKNOWN"),
        "_tier":      outcome.get("conviction_tier", "UNKNOWN"),
        "_ts":        outcome.get("resolved_at", outcome.get("timestamp", "")),
        "score":      score,
    }

    for key in ALL_FEATURES:
        if key == "score":
            continue
        if key in layer:
            try:
                row[key] = float(layer[key])
            except (TypeError, ValueError):
                pass

    # Precisa de pelo menos 2 detectores além de score
    n_detectors = sum(1 for k in row if not k.startswith("_") and k != "score")
    if n_detectors < 2:
        return None

    return row


# ---------------------------------------------------------------------------
# Matemática estatística — stdlib puro
# ---------------------------------------------------------------------------

def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _variance(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return sum((v - m) ** 2 for v in vals) / (len(vals) - 1)


def _std(vals: list[float]) -> float:
    return math.sqrt(_variance(vals))


def _pearson_pointbiserial(labels: list[float], values: list[float]) -> float:
    """
    Correlação ponto-bisserial: equivalente à correlação de Pearson quando
    uma variável é binária (0/1). Range: [-1, +1].
    """
    n = len(labels)
    if n < 4:
        return 0.0
    n1 = sum(labels)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return 0.0
    m1 = _mean([c for b, c in zip(labels, values) if b == 1])
    m0 = _mean([c for b, c in zip(labels, values) if b == 0])
    sd = _std(values)
    if sd == 0:
        return 0.0
    return (m1 - m0) / sd * math.sqrt(n1 * n0 / (n * n))


def _cohen_d(wins: list[float], losses: list[float]) -> float:
    """
    Cohen's d: tamanho do efeito padronizado entre WIN e LOSS.
    d < 0.2 negligível, 0.2-0.5 pequeno, 0.5-0.8 médio, > 0.8 grande.
    """
    if not wins or not losses:
        return 0.0
    m1, m0 = _mean(wins), _mean(losses)
    v1, v0 = _variance(wins), _variance(losses)
    n1, n0 = len(wins), len(losses)
    if n1 + n0 <= 2:
        return 0.0
    pooled = math.sqrt((v1 * (n1 - 1) + v0 * (n0 - 1)) / (n1 + n0 - 2))
    if pooled == 0:
        return 0.0
    return (m1 - m0) / pooled


def _mann_whitney_auc(wins: list[float], losses: list[float]) -> float:
    """
    AUC via Mann-Whitney U statistic.
    AUC = P(score_win > score_loss) para par aleatório (win, loss).
    AUC = U / (n_win * n_loss)
    0.5 = aleatório, > 0.7 = bom, > 0.8 = excelente.
    """
    nw = len(wins)
    nl = len(losses)
    if nw == 0 or nl == 0:
        return 0.5
    u = 0.0
    for w in wins:
        for lo in losses:
            if w > lo:
                u += 1.0
            elif w == lo:
                u += 0.5
    return u / (nw * nl)


def _information_value(labels: list[float], values: list[float], n_bins: int = 10) -> float:
    """
    Information Value (IV) via Weight of Evidence (WoE) em n_bins uniformes.
    IV < 0.02: inútil, 0.02-0.10: fraco, 0.10-0.30: moderado, > 0.30: forte.
    """
    n = len(values)
    if n < 4:
        return 0.0

    total_wins   = float(sum(labels))
    total_losses = float(n - total_wins)
    if total_wins == 0 or total_losses == 0:
        return 0.0

    vmin = min(values)
    vmax = max(values)
    if vmax == vmin:
        return 0.0

    bin_width = (vmax - vmin) / n_bins

    # Agrupar labels em bins
    bins: list[list[float]] = [[] for _ in range(n_bins)]
    for v, lbl in zip(values, labels):
        idx = min(int((v - vmin) / bin_width), n_bins - 1)
        bins[idx].append(lbl)

    iv = 0.0
    for bin_labels in bins:
        if not bin_labels:
            continue
        n_win  = float(sum(bin_labels))
        n_loss = float(len(bin_labels)) - n_win

        # Laplace smoothing para evitar log(0)
        p_win  = max(n_win,  0.5) / total_wins
        p_loss = max(n_loss, 0.5) / total_losses

        woe = math.log(p_win / p_loss)
        iv += (p_win - p_loss) * woe

    return iv


def _composite_score(rpb: float, d: float, auc: float, iv: float) -> float:
    """
    Composite score [0, 1] combinando as 4 métricas com pesos distintos.

    Normalização:
      |r_pb|         → [0, 1]  (já limitado a 1 por definição)
      |d| / 2        → [0, 1]  (d=2 é efeito muito grande)
      |auc - 0.5| * 2 → [0, 1] (0.5=aleatório, 1.0=perfeito)
      iv / 0.5       → [0, 1]  (iv > 0.5 é forte)

    Pesos: r_pb=0.25, d=0.35, auc=0.25, iv=0.15
    """
    s_rpb = min(abs(rpb), 1.0)
    s_d   = min(abs(d) / 2.0, 1.0)
    s_auc = min(abs(auc - 0.5) * 2, 1.0)
    s_iv  = min(abs(iv) / 0.5, 1.0)
    return round(_W_RPB * s_rpb + _W_D * s_d + _W_AUC * s_auc + _W_IV * s_iv, 4)


def _composite_label(score: float) -> str:
    """Retorna rótulo textual do composite score."""
    for threshold, label in sorted(COMPOSITE_LABELS.items(), reverse=True):
        if score >= threshold:
            return label
    return "negligível"


def _quartile_win_rates(labels: list[float], values: list[float]) -> list[dict]:
    """Win rate por quartil de uma feature."""
    if len(values) < 8:
        return []
    paired = sorted(zip(values, labels))
    q = len(paired) // 4
    result = []
    for i, lbl in enumerate(["Q1 (baixo)", "Q2", "Q3", "Q4 (alto)"]):
        chunk = paired[i * q: (i + 1) * q] if i < 3 else paired[i * q:]
        wins  = sum(1 for _, l in chunk if l == 1)
        total = len(chunk)
        result.append({
            "quartil":  lbl,
            "win_rate": round(wins / total * 100, 1) if total else 0.0,
            "n":        total,
        })
    return result


# ---------------------------------------------------------------------------
# Análise de um conjunto de rows
# ---------------------------------------------------------------------------

def _analyze_rows(rows: list[dict], min_n: int = MIN_SUBGROUP) -> dict | None:
    """
    Calcula todas as 5 métricas para um conjunto de rows.
    Retorna None se dados insuficientes.
    """
    if len(rows) < min_n:
        return None

    labels    = [r["_label"] for r in rows]
    n_wins    = int(sum(labels))
    n_losses  = len(rows) - n_wins
    win_rate  = round(n_wins / len(rows) * 100, 1)

    # Score composto — separação
    scores     = [r["score"] for r in rows]
    w_scores   = [r["score"] for r in rows if r["_label"] == 1]
    l_scores   = [r["score"] for r in rows if r["_label"] == 0]
    score_sep  = round(_mean(w_scores) - _mean(l_scores), 2) if w_scores and l_scores else 0.0

    # Features disponíveis neste subgrupo (excluir metadados)
    feat_names = sorted({k for r in rows for k in r if not k.startswith("_")})

    features: list[dict] = []
    for feat in feat_names:
        vals = [r[feat] for r in rows if feat in r]
        lbls = [r["_label"] for r in rows if feat in r]
        if len(vals) < min_n:
            continue

        wins_v  = [v for v, l in zip(vals, lbls) if l == 1]
        loss_v  = [v for v, l in zip(vals, lbls) if l == 0]

        rpb  = _pearson_pointbiserial(lbls, vals)
        d    = _cohen_d(wins_v, loss_v)
        auc  = _mann_whitney_auc(wins_v, loss_v)
        iv   = _information_value(lbls, vals)
        comp = _composite_score(rpb, d, auc, iv)

        vmin = min(vals)
        vmax = max(vals)
        sep_pct = ((_mean(wins_v) - _mean(loss_v)) / (vmax - vmin) * 100
                   if (vmax - vmin) > 0 else 0.0)

        features.append({
            "feature":         feat,
            "point_biserial":  round(rpb, 4),
            "cohen_d":         round(d, 4),
            "auc":             round(auc, 4),
            "iv":              round(iv, 4),
            "composite":       comp,
            "composite_label": _composite_label(comp),
            "mean_wins":       round(_mean(wins_v), 3),
            "mean_losses":     round(_mean(loss_v), 3),
            "separation_pct":  round(sep_pct, 2),
            "n_wins":          len(wins_v),
            "n_losses":        len(loss_v),
            "quartile_win_rates": _quartile_win_rates(lbls, vals),
        })

    # Ordenar por composite decrescente
    features.sort(key=lambda x: x["composite"], reverse=True)

    return {
        "n_total":         len(rows),
        "n_wins":          n_wins,
        "n_losses":        n_losses,
        "win_rate_pct":    win_rate,
        "score_separation": score_sep,
        "features":        features,
    }


# ---------------------------------------------------------------------------
# Classe pública
# ---------------------------------------------------------------------------

class FeatureImportanceAnalyzer:
    """
    Analisa quais detectores (layer_scores) predizem WIN vs LOSS.
    READ-ONLY: nunca modifica outcomes nem scoring engines.

    API pública:
      analyze(outcomes=None) → dict completo
      ranking(source=None)   → lista ordenada por composite
      top_predictors(n, source=None) → nomes das N melhores features
      should_optimize(min_composite=0.20) → bool
      export_json(path=None) → persiste resultado em JSON
    """

    def __init__(self) -> None:
        self._last_result: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    def analyze(self, outcomes: list[dict] | None = None) -> dict[str, Any]:
        """
        Roda análise completa. Retorna dict com métricas globais e por segmento.

        Parâmetros:
          outcomes: lista de outcomes (opcional; carrega de disco se None)

        Retorna:
          status:             "ok" | "insufficient_data" | "no_data"
          n_total, n_wins, n_losses, win_rate_pct
          score_separation:   mean(score_win) - mean(score_loss)
          features:           lista por composite decrescente (global)
          by_source:          {"LONG": ..., "SHORT": ...}
          by_conviction_tier: {"EXTREME": ..., ...}
          by_score_band:      {"0-25": ..., ...}
          timestamp:          ISO 8601
        """
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()

        raw = outcomes if outcomes is not None else _load_resolved_outcomes()
        resolved = [o for o in raw if o.get("status") in ("WIN", "LOSS")]

        n_wins   = sum(1 for o in resolved if o["status"] == "WIN")
        n_losses = len(resolved) - n_wins

        result: dict[str, Any] = {
            "timestamp":  ts,
            "n_total":    len(resolved),
            "n_wins":     n_wins,
            "n_losses":   n_losses,
            "win_rate_pct": round(n_wins / len(resolved) * 100, 1) if resolved else 0.0,
        }

        if len(resolved) < MIN_OUTCOMES:
            result["status"]       = "insufficient_data"
            result["min_required"] = MIN_OUTCOMES
            result["features"]     = []
            result["by_source"]    = {}
            result["by_conviction_tier"] = {}
            result["by_score_band"]      = {}
            result["score_separation"]   = 0.0
            self._last_result = result
            logger.info("[FI] Dados insuficientes: %d/%d requeridos", len(resolved), MIN_OUTCOMES)
            return result

        # Extrai rows de features
        rows: list[dict] = []
        for o in resolved:
            row = _extract_row(o)
            if row is not None:
                rows.append(row)

        if not rows:
            result["status"]              = "no_data"
            result["features"]            = []
            result["by_source"]           = {}
            result["by_conviction_tier"]  = {}
            result["by_score_band"]       = {}
            result["score_separation"]    = 0.0
            self._last_result = result
            return result

        # ── Análise global ──────────────────────────────────────────────
        global_stats = _analyze_rows(rows, min_n=MIN_OUTCOMES)
        if global_stats is None:
            result["status"] = "insufficient_data"
            result["features"] = []
            result["by_source"] = {}
            result["by_conviction_tier"] = {}
            result["by_score_band"] = {}
            result["score_separation"] = 0.0
            self._last_result = result
            return result

        result["status"]            = "ok"
        result["score_separation"]  = global_stats["score_separation"]
        result["features"]          = global_stats["features"]

        # ── Por source (LONG / SHORT) ───────────────────────────────────
        by_source: dict[str, dict] = {}
        for direction in ("LONG", "SHORT"):
            sub = [r for r in rows if r.get("_direction") == direction]
            stats = _analyze_rows(sub, min_n=MIN_SUBGROUP)
            if stats:
                by_source[direction] = stats
        result["by_source"] = by_source

        # ── Por conviction_tier ─────────────────────────────────────────
        by_tier: dict[str, dict] = {}
        tiers = sorted({r["_tier"] for r in rows})
        for tier in tiers:
            sub = [r for r in rows if r.get("_tier") == tier]
            stats = _analyze_rows(sub, min_n=MIN_SUBGROUP)
            if stats:
                by_tier[tier] = stats
        result["by_conviction_tier"] = by_tier

        # ── Por score_band ──────────────────────────────────────────────
        by_band: dict[str, dict] = {}
        for band_name, lo, hi in SCORE_BANDS:
            sub = [r for r in rows if lo <= r.get("score", 0) < hi]
            stats = _analyze_rows(sub, min_n=MIN_SUBGROUP)
            if stats:
                by_band[band_name] = stats
        result["by_score_band"] = by_band

        self._last_result = result
        logger.info(
            "[FI] Análise concluída: %d trades, %d features, sep=%.2f pts",
            len(rows),
            len(result["features"]),
            result["score_separation"],
        )
        return result

    # ------------------------------------------------------------------
    def ranking(self, source: str | None = None) -> list[dict]:
        """
        Retorna features ordenadas por composite decrescente.
        source: "LONG" | "SHORT" | None (global)
        """
        if not self._last_result:
            return []
        if source and source in (self._last_result.get("by_source") or {}):
            return self._last_result["by_source"][source]["features"]
        return self._last_result.get("features", [])

    # ------------------------------------------------------------------
    def top_predictors(self, n: int = 3, source: str | None = None) -> list[str]:
        """Retorna nomes das N features mais preditivas (por composite)."""
        return [f["feature"] for f in self.ranking(source)[:n]]

    # ------------------------------------------------------------------
    def should_optimize(self, min_composite: float = 0.20) -> bool:
        """
        Retorna True se houver pelo menos uma feature com composite >= min_composite.
        Indica que há sinal preditivo suficiente para justificar otimização.
        """
        if not self._last_result or self._last_result.get("status") != "ok":
            return False
        features = self._last_result.get("features", [])
        return any(f["composite"] >= min_composite for f in features)

    # ------------------------------------------------------------------
    def last_result(self) -> dict[str, Any] | None:
        """Retorna último resultado de analyze()."""
        return self._last_result

    # ------------------------------------------------------------------
    def export_json(self, path: pathlib.Path | None = None) -> pathlib.Path:
        """
        Persiste último resultado em JSON.
        Default: dashboard/ml_feature_importance.json
        """
        if not self._last_result:
            raise RuntimeError("[FI] Nada para exportar — chame analyze() primeiro")
        out = path or RESULTS_FILE
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self._last_result, indent=2, default=str), encoding="utf-8")
        logger.info("[FI] Resultado exportado: %s", out)
        return out
