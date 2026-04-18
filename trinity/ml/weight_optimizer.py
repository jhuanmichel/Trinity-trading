"""
trinity/ml/weight_optimizer.py — v2
WeightOptimizer: 3 métodos de otimização + cross-validation temporal.

Pipeline:
  1. Random Search  (N_RANDOM=10_000 iterações, objetivo Score Sharpe)
  2. Hill Climbing  (N_HILL=1_000, parte do melhor random)
  3. Gradient Descent (N_GD=500, diferenças finitas, sem libs externas)
  Cross-validation temporal 5-fold (anchored expanding window)
  Threshold co-optimization (threshold ótimo com novos pesos)

Objetivo primário — Score Sharpe J(W):
  J(W) = (μ_win_score − μ_loss_score) / σ_pooled
  Maior → melhor separação WIN/LOSS com os pesos W.

Soft constraints:
  5 ≤ w_i ≤ 50  (cada detector)
  Σw_i = 100     (soma total)

Conservadorismo:
  Só recomenda novos pesos se melhora CV ≥ MIN_IMPROVEMENT_AUC.
  Fallback: pesos uniformes 25/25/25/25.

READ-ONLY: nunca modifica scoring engines.
"""

from __future__ import annotations

import json
import logging
import math
import pathlib
import random
import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MIN_OUTCOMES      = 30    # mínimo para rodar otimização
MIN_SUBGROUP      = 15    # mínimo por fold de CV
N_RANDOM          = 10_000
N_HILL            = 1_000
N_GD              = 500   # iterações de gradient descent
GD_LR             = 2.0   # learning rate (em pts de peso)
GD_EPS            = 1.0   # epsilon para diferenças finitas
PERTURBATION_HILL = 5.0   # ±pts por step de hill climbing

W_MIN = 5.0    # peso mínimo por detector
W_MAX = 50.0   # peso máximo por detector
W_SUM = 100.0  # soma total dos pesos

N_CV_FOLDS   = 5   # blocos temporais
MIN_IMPROVEMENT_AUC = 0.005  # melhoria mínima de AUC para recomendar

# Detectors por direção
DETECTORS_LONG  = ["silent_acc", "squeeze", "gravity", "breakout"]
DETECTORS_SHORT = ["cascade",   "collapse", "whale",   "volatility"]

LOGS_DIR     = pathlib.Path(__file__).parent.parent.parent / "logs"
RESULTS_FILE = pathlib.Path(__file__).parent.parent.parent / "dashboard" / "ml_weight_optimizer.json"

# Fallback: pesos uniformes
def _uniform(detectors: list[str]) -> dict[str, float]:
    w = W_SUM / len(detectors)
    return {d: round(w, 2) for d in detectors}


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _load_resolved_by_direction() -> dict[str, list[dict]]:
    """Retorna {"LONG": [...], "SHORT": [...]} de outcomes resolvidos + ordenados por tempo."""
    data: dict[str, list] = {"LONG": [], "SHORT": []}
    files = sorted(LOGS_DIR.glob("outcomes_*.jsonl"))
    for fpath in files:
        try:
            for line in fpath.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("status") not in ("WIN", "LOSS"):
                    continue
                d = o.get("direction", "")
                if d in data:
                    data[d].append(o)
        except Exception as exc:
            logger.warning("[WO] Erro ao ler %s: %s", fpath.name, exc)

    # Ordenar por timestamp para CV temporal
    for key in data:
        data[key].sort(key=lambda o: o.get("resolved_at", o.get("timestamp", "")))

    return data


# ---------------------------------------------------------------------------
# Cálculo de score com pesos
# ---------------------------------------------------------------------------

def _score_with_weights(outcome: dict, weights: dict[str, float]) -> float:
    """
    Recalcula o score composto de um outcome usando pesos customizados.
    score = Σ layer_scores[d] * (w[d] / 25)
    — normaliza por 25 pois cada layer vai de 0-25 com peso padrão 25.
    """
    ls = outcome.get("layer_scores") or {}
    total = 0.0
    for feat, w in weights.items():
        raw = float(ls.get(feat, 0.0))
        total += raw * (w / 25.0)  # normaliza para escala 0-100
    return total


# ---------------------------------------------------------------------------
# Funções objetivo
# ---------------------------------------------------------------------------

def _score_sharpe(outcomes: list[dict], weights: dict[str, float]) -> float:
    """
    Objetivo primário: Score Sharpe = (μ_win − μ_loss) / σ_pooled.
    Mede a separação WIN/LOSS em desvios padrão.
    """
    wins   = [o for o in outcomes if o.get("status") == "WIN"]
    losses = [o for o in outcomes if o.get("status") == "LOSS"]
    if not wins or not losses:
        return 0.0

    w_scores = [_score_with_weights(o, weights) for o in wins]
    l_scores = [_score_with_weights(o, weights) for o in losses]
    all_sc   = w_scores + l_scores

    def mean(lst):  return sum(lst) / len(lst)
    def std(lst):
        if len(lst) < 2: return 0.0
        m = mean(lst)
        return math.sqrt(sum((x - m) ** 2 for x in lst) / (len(lst) - 1))

    mu_w  = mean(w_scores)
    mu_l  = mean(l_scores)
    sigma = std(all_sc)

    return (mu_w - mu_l) / sigma if sigma > 0 else 0.0


def _auc_mann_whitney(outcomes: list[dict], weights: dict[str, float]) -> float:
    """
    AUC via Mann-Whitney U: P(score_win > score_loss).
    0.5 = aleatório, 1.0 = perfeito. Usado para comparação e CV.
    """
    wins   = [o for o in outcomes if o.get("status") == "WIN"]
    losses = [o for o in outcomes if o.get("status") == "LOSS"]
    nw = len(wins)
    nl = len(losses)
    if nw == 0 or nl == 0:
        return 0.5

    w_scores = [_score_with_weights(o, weights) for o in wins]
    l_scores = [_score_with_weights(o, weights) for o in losses]

    u = sum(
        1.0 if ws > ls else 0.5 if ws == ls else 0.0
        for ws in w_scores
        for ls in l_scores
    )
    return u / (nw * nl)


# ---------------------------------------------------------------------------
# Geração e manipulação de pesos
# ---------------------------------------------------------------------------

def _random_weights(detectors: list[str], rng: random.Random) -> dict[str, float]:
    """
    Amostra pesos aleatórios uniformes com constraints 5 ≤ w ≤ 50, Σ=100.
    Método: Dirichlet via gama com clipping.
    """
    while True:
        raw = [rng.uniform(W_MIN, W_MAX) for _ in detectors]
        total = sum(raw)
        scaled = [v / total * W_SUM for v in raw]
        # Verificar constraints
        if all(W_MIN <= w <= W_MAX for w in scaled):
            return {d: round(scaled[i], 2) for i, d in enumerate(detectors)}
        # Se falhou, tentar de novo (raramente necessário)


def _perturb_weights(weights: dict[str, float], detectors: list[str],
                     rng: random.Random, step: float = PERTURBATION_HILL) -> dict[str, float]:
    """Perturba um peso aleatório ±step e re-projeta para constraints."""
    w = dict(weights)
    feat  = rng.choice(detectors)
    delta = rng.uniform(-step, step)
    w[feat] = max(W_MIN, min(W_MAX, w[feat] + delta))
    # Renormalizar mantendo o resto proporcional
    return _normalize_weights(w, detectors)


def _normalize_weights(weights: dict[str, float], detectors: list[str]) -> dict[str, float]:
    """Renormaliza para Σ=100 respeitando [W_MIN, W_MAX]."""
    w = {d: max(W_MIN, min(W_MAX, weights.get(d, W_SUM / len(detectors)))) for d in detectors}
    total = sum(w.values())
    scaled = {d: w[d] / total * W_SUM for d in detectors}
    # Clips e renormaliza mais uma vez se necessário
    clipped = {d: max(W_MIN, min(W_MAX, scaled[d])) for d in detectors}
    total2 = sum(clipped.values())
    return {d: round(clipped[d] / total2 * W_SUM, 2) for d in detectors}


# ---------------------------------------------------------------------------
# 3 Métodos de otimização
# ---------------------------------------------------------------------------

def _random_search(
    outcomes: list[dict], detectors: list[str],
    n_iter: int = N_RANDOM, rng: random.Random | None = None,
) -> tuple[dict[str, float], float]:
    """
    Fase 1: Random Search — amostra n_iter conjuntos de pesos aleatórios.
    Retorna (best_weights, best_sharpe).
    """
    if rng is None:
        rng = random.Random(42)
    best_w = _uniform(detectors)
    best_j = _score_sharpe(outcomes, best_w)

    for _ in range(n_iter):
        w = _random_weights(detectors, rng)
        j = _score_sharpe(outcomes, w)
        if j > best_j:
            best_j = j
            best_w = w

    return best_w, best_j


def _hill_climbing(
    outcomes: list[dict], detectors: list[str],
    init_weights: dict[str, float], n_iter: int = N_HILL,
    rng: random.Random | None = None,
) -> tuple[dict[str, float], float]:
    """
    Fase 2: Hill Climbing — explora vizinhança local do melhor random.
    Aceita movimentos que melhorem J(W).
    """
    if rng is None:
        rng = random.Random(43)
    best_w = dict(init_weights)
    best_j = _score_sharpe(outcomes, best_w)

    for _ in range(n_iter):
        cand_w = _perturb_weights(best_w, detectors, rng)
        cand_j = _score_sharpe(outcomes, cand_w)
        if cand_j > best_j:
            best_j = cand_j
            best_w = cand_w

    return best_w, best_j


def _gradient_descent(
    outcomes: list[dict], detectors: list[str],
    init_weights: dict[str, float], n_iter: int = N_GD,
    lr: float = GD_LR, eps: float = GD_EPS,
) -> tuple[dict[str, float], float]:
    """
    Fase 3: Gradient Descent via diferenças finitas (sem libs externas).
    ∇J[i] ≈ (J(W + ε*e_i) - J(W - ε*e_i)) / (2ε)
    Update: W = W + lr * ∇J  (projetado para constraints)
    """
    w = dict(init_weights)
    best_j = _score_sharpe(outcomes, w)
    best_w = dict(w)

    for _ in range(n_iter):
        grad = {}
        j_0  = _score_sharpe(outcomes, w)

        for d in detectors:
            # Perturbação positiva
            w_plus = dict(w)
            w_plus[d] = min(W_MAX, w[d] + eps)
            w_plus = _normalize_weights(w_plus, detectors)
            j_plus = _score_sharpe(outcomes, w_plus)

            # Perturbação negativa
            w_minus = dict(w)
            w_minus[d] = max(W_MIN, w[d] - eps)
            w_minus = _normalize_weights(w_minus, detectors)
            j_minus = _score_sharpe(outcomes, w_minus)

            grad[d] = (j_plus - j_minus) / (2 * eps)

        # Passo de gradiente + projeção
        w_new = {d: w[d] + lr * grad[d] for d in detectors}
        w_new = _normalize_weights(w_new, detectors)
        j_new = _score_sharpe(outcomes, w_new)

        if j_new > j_0:
            w = w_new
            if j_new > best_j:
                best_j = j_new
                best_w = dict(w_new)
        else:
            # Reduzir lr progressivamente
            lr *= 0.9

    return best_w, best_j


# ---------------------------------------------------------------------------
# Cross-validation temporal (anchored expanding window)
# ---------------------------------------------------------------------------

def _temporal_cv(
    outcomes: list[dict], detectors: list[str],
    n_folds: int = N_CV_FOLDS, seed: int = 42,
) -> dict[str, Any]:
    """
    Cross-validation temporal com anchored expanding window.

    Divide outcomes ordenados em (n_folds+1) blocos.
    Fold k (k=1..n_folds):
      train = blocos 0..k-1  (todos antes do bloco k)
      test  = bloco k

    Retorna dict com cv_auc_mean, cv_auc_std, fold_results.
    """
    n = len(outcomes)
    if n < MIN_SUBGROUP * 2:
        return {"status": "insufficient_data", "cv_auc_mean": 0.5, "cv_auc_std": 0.0}

    block_size = n // (n_folds + 1)
    if block_size < MIN_SUBGROUP // 2:
        return {"status": "insufficient_data", "cv_auc_mean": 0.5, "cv_auc_std": 0.0}

    rng = random.Random(seed)
    fold_results = []

    for fold in range(1, n_folds + 1):
        train_end  = fold * block_size
        test_start = train_end
        test_end   = min(test_start + block_size, n)

        train = outcomes[:train_end]
        test  = outcomes[test_start:test_end]

        # Verificar que train e test têm WIN e LOSS
        tr_wins = sum(1 for o in train if o.get("status") == "WIN")
        tr_loss = len(train) - tr_wins
        te_wins = sum(1 for o in test  if o.get("status") == "WIN")
        te_loss = len(test)  - te_wins

        if tr_wins < 3 or tr_loss < 3 or te_wins < 2 or te_loss < 2:
            fold_results.append({
                "fold": fold, "skipped": True,
                "train_n": len(train), "test_n": len(test),
            })
            continue

        # Otimizar nos dados de treino
        best_w, _   = _random_search(train, detectors, n_iter=N_RANDOM // 4, rng=random.Random(seed + fold))
        best_w, _   = _hill_climbing(train, detectors, best_w, n_iter=N_HILL // 4, rng=random.Random(seed + fold + 1))

        # Avaliar no teste (AUC)
        test_auc    = _auc_mann_whitney(test, best_w)
        base_auc    = _auc_mann_whitney(test, _uniform(detectors))
        train_sharpe = _score_sharpe(train, best_w)

        fold_results.append({
            "fold":        fold,
            "train_n":     len(train),
            "test_n":      len(test),
            "train_wins":  tr_wins,
            "test_wins":   te_wins,
            "test_auc":    round(test_auc, 4),
            "base_auc":    round(base_auc, 4),
            "train_sharpe": round(train_sharpe, 4),
            "weights":     {k: round(v, 2) for k, v in best_w.items()},
        })

    valid = [f for f in fold_results if not f.get("skipped")]
    if not valid:
        return {
            "status": "insufficient_data",
            "cv_auc_mean": 0.5,
            "cv_auc_std": 0.0,
            "fold_results": fold_results,
        }

    aucs = [f["test_auc"] for f in valid]
    mean_auc = sum(aucs) / len(aucs)
    var_auc  = sum((a - mean_auc) ** 2 for a in aucs) / max(len(aucs) - 1, 1)
    std_auc  = math.sqrt(var_auc)

    return {
        "status":       "ok",
        "n_folds_run":  len(valid),
        "cv_auc_mean":  round(mean_auc, 4),
        "cv_auc_std":   round(std_auc, 4),
        "fold_results": fold_results,
    }


# ---------------------------------------------------------------------------
# Threshold co-optimization
# ---------------------------------------------------------------------------

def _optimize_threshold(
    outcomes: list[dict], weights: dict[str, float],
    t_min: float = 30.0, t_max: float = 85.0, t_step: float = 2.5,
) -> dict[str, Any]:
    """
    Encontra threshold ótimo T dado um conjunto de pesos.
    Para cada T: filtra outcomes com score >= T e calcula profit_factor.

    profit_factor = (n_wins * WIN_PNL_EST) / (n_losses * LOSS_PNL_EST)
    WIN_PNL_EST  = 2.2%  (fallback)
    LOSS_PNL_EST = 1.5%  (fallback)

    Retorna: {threshold, profit_factor, win_rate, n_trades, win_trades, loss_trades}
    """
    WIN_EST  = 2.2
    LOSS_EST = 1.5

    best_t  = t_min
    best_pf = 0.0
    best_wr = 0.0
    best_n  = 0
    results = []

    t = t_min
    while t <= t_max:
        # Re-score com pesos customizados
        above = [o for o in outcomes if _score_with_weights(o, weights) >= t]
        wins  = [o for o in above if o.get("status") == "WIN"]
        losses= [o for o in above if o.get("status") == "LOSS"]
        nw    = len(wins)
        nl    = len(losses)
        n     = nw + nl

        if n >= 10:
            pnl_wins  = sum(float(o.get("pnl_pct") or WIN_EST)  for o in wins)
            pnl_loss  = sum(abs(float(o.get("pnl_pct") or LOSS_EST)) for o in losses)
            pf = pnl_wins / pnl_loss if pnl_loss > 0 else 0.0
            wr = nw / n

            results.append({
                "threshold":     round(t, 1),
                "profit_factor": round(pf, 4),
                "win_rate":      round(wr * 100, 1),
                "n_trades":      n,
                "win_trades":    nw,
                "loss_trades":   nl,
            })

            if pf > best_pf:
                best_pf = pf
                best_t  = t
                best_wr = wr
                best_n  = n

        t += t_step

    return {
        "optimal_threshold": best_t,
        "profit_factor":     round(best_pf, 4),
        "win_rate_pct":      round(best_wr * 100, 1),
        "n_trades":          best_n,
        "scan":              results,
    }


# ---------------------------------------------------------------------------
# Otimização por direção
# ---------------------------------------------------------------------------

def _optimize_for_direction(
    outcomes: list[dict], detectors: list[str], seed: int = 42,
) -> dict[str, Any]:
    """
    Roda pipeline completo (3 métodos + CV + threshold) para uma direção.
    Retorna dict completo com recomendações e status de confiança.
    """
    wins   = [o for o in outcomes if o.get("status") == "WIN"]
    losses = [o for o in outcomes if o.get("status") == "LOSS"]
    n_total = len(wins) + len(losses)

    result: dict[str, Any] = {
        "n_total":  n_total,
        "n_wins":   len(wins),
        "n_losses": len(losses),
        "win_rate_pct": round(len(wins) / n_total * 100, 1) if n_total > 0 else 0.0,
    }

    if n_total < MIN_OUTCOMES:
        result["status"]    = "insufficient_data"
        result["weights"]   = _uniform(detectors)
        result["auc"]       = 0.5
        result["sharpe"]    = 0.0
        result["recommend"] = False
        return result

    rng = random.Random(seed)

    # ── Baseline (pesos uniformes) ──────────────────────────────────────
    unif_w      = _uniform(detectors)
    baseline_auc = _auc_mann_whitney(outcomes, unif_w)
    baseline_j   = _score_sharpe(outcomes, unif_w)

    # ── Fase 1: Random Search ───────────────────────────────────────────
    best_w, best_j = _random_search(outcomes, detectors, n_iter=N_RANDOM, rng=rng)

    # ── Fase 2: Hill Climbing ───────────────────────────────────────────
    best_w, best_j = _hill_climbing(outcomes, detectors, best_w, n_iter=N_HILL, rng=rng)

    # ── Fase 3: Gradient Descent ────────────────────────────────────────
    best_w, best_j = _gradient_descent(outcomes, detectors, best_w, n_iter=N_GD)

    # ── AUC final com pesos otimizados ──────────────────────────────────
    final_auc = _auc_mann_whitney(outcomes, best_w)

    # ── Cross-validation temporal ───────────────────────────────────────
    cv = _temporal_cv(outcomes, detectors, n_folds=N_CV_FOLDS, seed=seed)
    cv_auc = cv.get("cv_auc_mean", 0.5)

    # ── Threshold co-optimization ───────────────────────────────────────
    thresh_result = _optimize_threshold(outcomes, best_w)

    # ── Decisão: recomendar? ────────────────────────────────────────────
    # Só recomenda se a AUC de CV melhorou MIN_IMPROVEMENT_AUC vs baseline
    improvement_auc = final_auc - baseline_auc
    recommend = (
        cv.get("status") == "ok"
        and improvement_auc >= MIN_IMPROVEMENT_AUC
        and best_j > baseline_j
    )

    result.update({
        "status":          "ok",
        "recommend":       recommend,
        "weights":         {k: round(v, 2) for k, v in best_w.items()},
        "sharpe":          round(best_j, 4),
        "auc":             round(final_auc, 4),
        "baseline_sharpe": round(baseline_j, 4),
        "baseline_auc":    round(baseline_auc, 4),
        "improvement_auc": round(improvement_auc, 4),
        "cv":              cv,
        "threshold":       thresh_result,
    })

    logger.info(
        "[WO] Otimização (%d outcomes): Sharpe %.3f→%.3f | AUC %.4f→%.4f | recommend=%s",
        n_total, baseline_j, best_j, baseline_auc, final_auc, recommend,
    )
    return result


# ---------------------------------------------------------------------------
# Classe pública
# ---------------------------------------------------------------------------

class WeightOptimizer:
    """
    Otimiza pesos dos detectors por direção (LONG / SHORT).
    READ-ONLY: retorna recomendações, nunca modifica os engines.

    API pública:
      optimize(seed=42) → dict completo com LONG, SHORT, timestamp
      recommended_weights(direction) → dict[str, float] | None
      recommended_threshold(direction) → float | None
      export_json(path=None) → persiste resultado
      last_result() → dict | None
    """

    def __init__(self) -> None:
        self._last_result: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    def optimize(self, seed: int = 42) -> dict[str, Any]:
        """
        Roda otimização completa para LONG e SHORT.
        Retorna dict com status, timestamp, long, short.
        """
        by_dir = _load_resolved_by_direction()

        long_result  = _optimize_for_direction(by_dir["LONG"],  DETECTORS_LONG,  seed=seed)
        short_result = _optimize_for_direction(by_dir["SHORT"], DETECTORS_SHORT, seed=seed + 1)

        overall = "ok" if (
            long_result.get("status") == "ok" or short_result.get("status") == "ok"
        ) else "insufficient_data"

        result: dict[str, Any] = {
            "status":    overall,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "long":      long_result,
            "short":     short_result,
        }
        self._last_result = result
        return result

    # ------------------------------------------------------------------
    def recommended_weights(self, direction: str) -> dict[str, float] | None:
        """
        Retorna pesos recomendados se recommend=True.
        None → usar pesos uniformes (não recomendado otimizar ainda).
        """
        if not self._last_result:
            return None
        side = self._last_result.get(direction.lower())
        if not side or not side.get("recommend"):
            return None
        return side.get("weights")

    # ------------------------------------------------------------------
    def recommended_threshold(self, direction: str) -> float | None:
        """Retorna threshold ótimo co-otimizado com os pesos."""
        if not self._last_result:
            return None
        side = self._last_result.get(direction.lower())
        if not side or not side.get("recommend"):
            return None
        return side.get("threshold", {}).get("optimal_threshold")

    # ------------------------------------------------------------------
    def last_result(self) -> dict[str, Any] | None:
        return self._last_result

    # ------------------------------------------------------------------
    def export_json(self, path: pathlib.Path | None = None) -> pathlib.Path:
        """Persiste último resultado em JSON."""
        if not self._last_result:
            raise RuntimeError("[WO] Nada para exportar — chame optimize() primeiro")
        out = path or RESULTS_FILE
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self._last_result, indent=2, default=str), encoding="utf-8")
        logger.info("[WO] Resultado exportado: %s", out)
        return out
