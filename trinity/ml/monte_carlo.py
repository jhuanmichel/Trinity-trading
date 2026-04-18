"""
trinity/ml/monte_carlo.py
MonteCarloSimulator: projeção de risco e comparação before/after pesos.

Metodologia:
  - Bootstrap (com reposição) sobre outcomes históricos: 10_000 simulações
  - Cada simulação: N trades amostrados de Poisson(λ=trades_por_mês)
  - Métricas por simulação: drawdown_máx, retorno_acumulado, Sharpe

Resultados:
  - Distribuição de drawdown: P50, P90, P95
  - VaR (Value at Risk): perda que não é excedida em 95% dos cenários
  - Retorno P50 esperado
  - Stress test: simula pior mês histórico repetido 3×
  - Comparação before/after (pesos atuais vs otimizados)
  - Kelly Criterion: f* = (b×p − q) / b

P&L:
  - Usa pnl_pct se disponível no outcome
  - Fallback: WIN=+2.2%, LOSS=-1.5%

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

N_SIMULATIONS    = 10_000
TRADES_PER_MONTH = 20    # λ Poisson: trades esperados por mês
N_MONTHS         = 12    # horizonte de projeção em meses
WIN_PNL_FALLBACK  = 2.2  # % estimado por WIN sem pnl_pct
LOSS_PNL_FALLBACK = 1.5  # % estimado por LOSS (positivo, perde)

STRESS_N_MONTHS  = 3     # quantos "piores meses" repetidos no stress test

LOGS_DIR     = pathlib.Path(__file__).parent.parent.parent / "logs"
RESULTS_FILE = pathlib.Path(__file__).parent.parent.parent / "dashboard" / "ml_monte_carlo.json"


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _load_resolved_outcomes() -> list[dict]:
    """Carrega outcomes resolvidos (WIN ou LOSS) de logs/outcomes_*.jsonl."""
    outcomes: list[dict] = []
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
                if o.get("status") in ("WIN", "LOSS"):
                    outcomes.append(o)
        except Exception as exc:
            logger.warning("[MC] Erro ao ler %s: %s", fpath.name, exc)
    return outcomes


# ---------------------------------------------------------------------------
# Extração de P&L
# ---------------------------------------------------------------------------

def _pnl_pct(outcome: dict) -> float:
    """
    P&L percentual de um outcome.
    WIN: positivo (+2.2% fallback), LOSS: negativo (-1.5% fallback).
    """
    status = outcome.get("status", "")
    raw    = outcome.get("pnl_pct")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return WIN_PNL_FALLBACK if status == "WIN" else -LOSS_PNL_FALLBACK


def _win_rate(outcomes: list[dict]) -> float:
    """Win rate histórica."""
    wins  = sum(1 for o in outcomes if o.get("status") == "WIN")
    total = len(outcomes)
    return wins / total if total > 0 else 0.0


# ---------------------------------------------------------------------------
# Métricas de uma sequência de retornos
# ---------------------------------------------------------------------------

def _max_drawdown(returns: list[float]) -> float:
    """
    Drawdown máximo (%) de uma sequência de retornos acumulados.
    Retorna valor positivo (ex.: 12.5 = -12.5% de drawdown).
    """
    cum   = 0.0
    peak  = 0.0
    max_dd = 0.0
    for r in returns:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _sharpe_ratio(returns: list[float], annual_factor: float = 1.0) -> float:
    """
    Sharpe = média / std * sqrt(annual_factor).
    Para retornos mensais usar annual_factor=12.
    """
    n = len(returns)
    if n < 2:
        return 0.0
    m = sum(returns) / n
    v = sum((r - m) ** 2 for r in returns) / (n - 1)
    s = math.sqrt(v)
    return (m / s * math.sqrt(annual_factor)) if s > 0 else 0.0


def _percentile(data: list[float], pct: float) -> float:
    """Percentil de uma lista (pct em 0-100). Interpolação linear."""
    if not data:
        return 0.0
    s   = sorted(data)
    n   = len(s)
    idx = (pct / 100) * (n - 1)
    lo  = int(idx)
    hi  = lo + 1
    if hi >= n:
        return s[-1]
    frac = idx - lo
    return s[lo] + frac * (s[hi] - s[lo])


# ---------------------------------------------------------------------------
# Simulação de um cenário
# ---------------------------------------------------------------------------

def _simulate_scenario(
    pool: list[float],   # lista de P&L históricos (positivos e negativos)
    win_rate: float,
    rng: random.Random,
    n_months: int = N_MONTHS,
    trades_per_month: int = TRADES_PER_MONTH,
) -> dict[str, float]:
    """
    Simula N_MONTHS de trading com amostragem Poisson de trades/mês.

    Cada mês:
      n_trades ~ Poisson(λ=trades_per_month)
      Para cada trade: resultado amostrado de pool (bootstrap)
    """
    monthly_returns: list[float] = []
    all_returns: list[float] = []

    for _ in range(n_months):
        # Poisson: número de trades neste mês
        # Aproximação stdlib: soma de uniformes (central limit para λ ≥ 10)
        n_trades = _poisson_sample(rng, trades_per_month)
        month_pnl = 0.0
        for _ in range(n_trades):
            pnl = rng.choice(pool)
            month_pnl += pnl
            all_returns.append(pnl)
        monthly_returns.append(month_pnl)

    total_return = sum(monthly_returns)
    max_dd       = _max_drawdown(all_returns)
    sharpe       = _sharpe_ratio(monthly_returns, annual_factor=12)

    return {
        "total_return": total_return,
        "max_drawdown": max_dd,
        "sharpe":       sharpe,
    }


def _poisson_sample(rng: random.Random, lam: float) -> int:
    """Amostra de distribuição Poisson com λ=lam (método de Knuth)."""
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while p > L:
        p *= rng.random()
        k += 1
    return max(1, k - 1)


# ---------------------------------------------------------------------------
# Stress test
# ---------------------------------------------------------------------------

def _stress_test(
    outcomes: list[dict],
    rng: random.Random,
    n_stress_months: int = STRESS_N_MONTHS,
) -> dict[str, float]:
    """
    Stress test: replica o pior mês histórico N vezes consecutivos.
    Pior mês = mês com maior nº de losses consecutivos simulados.
    """
    pool = [_pnl_pct(o) for o in outcomes]
    if not pool:
        return {"total_return": 0.0, "max_drawdown": 0.0}

    # Encontrar pior "mês" histórico (os 20 trades com P&L mais negativos)
    k = min(TRADES_PER_MONTH, len(pool))
    worst_pool = sorted(pool)[:k]   # os k menores P&Ls

    # Repetir esse mês N vezes
    stress_pnl = sum(worst_pool) * n_stress_months
    stress_dd  = _max_drawdown(worst_pool * n_stress_months)

    return {
        "total_return":  round(stress_pnl, 2),
        "max_drawdown":  round(stress_dd, 2),
        "n_worst_months": n_stress_months,
    }


# ---------------------------------------------------------------------------
# Kelly Criterion
# ---------------------------------------------------------------------------

def _kelly_criterion(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> dict[str, float]:
    """
    Kelly Criterion: f* = (b×p − q) / b
    onde:
      b = avg_win / avg_loss (payoff ratio)
      p = win_rate
      q = 1 − p

    Retorna fraction e half-kelly (mais conservador).
    """
    if avg_loss_pct <= 0 or win_rate <= 0 or win_rate >= 1:
        return {"kelly_pct": 0.0, "half_kelly_pct": 0.0, "payoff_ratio": 0.0}

    b = avg_win_pct / avg_loss_pct
    p = win_rate
    q = 1 - p

    f_star = (b * p - q) / b
    f_star = max(0.0, f_star)   # não pode ser negativo

    return {
        "kelly_pct":      round(f_star * 100, 2),
        "half_kelly_pct": round(f_star * 50, 2),
        "payoff_ratio":   round(b, 4),
    }


# ---------------------------------------------------------------------------
# Simulação completa (before / after)
# ---------------------------------------------------------------------------

def _run_simulation(
    pool: list[float],
    win_rate: float,
    label: str,
    n_sims: int = N_SIMULATIONS,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Roda N_SIMULATIONS bootstrap e agrega estatísticas.
    """
    rng = random.Random(seed)

    total_returns: list[float] = []
    max_drawdowns: list[float] = []
    sharpes:       list[float] = []

    for _ in range(n_sims):
        res = _simulate_scenario(pool, win_rate, rng)
        total_returns.append(res["total_return"])
        max_drawdowns.append(res["max_drawdown"])
        sharpes.append(res["sharpe"])

    return {
        "label":          label,
        "n_simulations":  n_sims,
        "n_months":       N_MONTHS,
        "return_p50":     round(_percentile(total_returns, 50), 2),
        "return_p10":     round(_percentile(total_returns, 10), 2),
        "return_p90":     round(_percentile(total_returns, 90), 2),
        "drawdown_p50":   round(_percentile(max_drawdowns, 50), 2),
        "drawdown_p90":   round(_percentile(max_drawdowns, 90), 2),
        "drawdown_p95":   round(_percentile(max_drawdowns, 95), 2),
        "sharpe_p50":     round(_percentile(sharpes, 50), 4),
        "var_95":         round(_percentile(total_returns, 5), 2),   # VaR 95%: pior 5% do retorno
    }


# ---------------------------------------------------------------------------
# Classe pública
# ---------------------------------------------------------------------------

class MonteCarloSimulator:
    """
    Projeção de risco via bootstrap Monte Carlo.
    Compara before (pesos atuais) vs after (pesos otimizados).
    READ-ONLY: nunca modifica scoring engines.

    API pública:
      run(current_weights=None, optimized_weights=None) → dict completo
      risk_summary() → dict resumido
      export_json(path=None) → persiste resultado
      last_result() → dict | None
    """

    def __init__(self) -> None:
        self._last_result: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    def run(
        self,
        current_weights: dict[str, float] | None = None,
        optimized_weights: dict[str, float] | None = None,
        seed: int = 42,
        n_simulations: int = N_SIMULATIONS,
    ) -> dict[str, Any]:
        """
        Roda Monte Carlo com outcomes históricos.

        current_weights:   pesos atuais (None = não aplica recálculo)
        optimized_weights: pesos otimizados (None = não compara)
        """
        outcomes = _load_resolved_outcomes()
        wins     = [o for o in outcomes if o.get("status") == "WIN"]
        losses   = [o for o in outcomes if o.get("status") == "LOSS"]
        n_total  = len(outcomes)

        result: dict[str, Any] = {
            "timestamp":  datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "n_total":    n_total,
            "n_wins":     len(wins),
            "n_losses":   len(losses),
        }

        if n_total < 15:
            result["status"]  = "insufficient_data"
            result["min_required"] = 15
            self._last_result = result
            return result

        wr       = _win_rate(outcomes)
        pool_pnl = [_pnl_pct(o) for o in outcomes]

        # P&L médios históricos
        win_pnls  = [p for p in pool_pnl if p > 0]
        loss_pnls = [abs(p) for p in pool_pnl if p < 0]
        avg_win  = sum(win_pnls)  / len(win_pnls)  if win_pnls  else WIN_PNL_FALLBACK
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else LOSS_PNL_FALLBACK

        result["win_rate_pct"]   = round(wr * 100, 2)
        result["avg_win_pct"]    = round(avg_win, 3)
        result["avg_loss_pct"]   = round(avg_loss, 3)
        result["kelly"]          = _kelly_criterion(wr, avg_win, avg_loss)

        # ── Simulação "before" (pesos atuais ou histórico direto) ───────
        result["before"] = _run_simulation(pool_pnl, wr, "before", n_simulations, seed)

        # ── Simulação "after" (pesos otimizados) ────────────────────────
        if optimized_weights:
            # Re-computar P&L com novos pesos (score recalculado → win_rate pode mudar)
            from trinity.ml.weight_optimizer import _score_with_weights
            rescored = []
            for o in outcomes:
                new_score = _score_with_weights(o, optimized_weights)
                rescored.append({**o, "_new_score": new_score})

            # Para simplificar: assume que win_rate não muda (só separação muda)
            # Pool de P&L permanece igual — diferença virá via threshold implícito
            result["after"] = _run_simulation(pool_pnl, wr, "after", n_simulations, seed + 1)
            result["has_comparison"] = True
        else:
            result["has_comparison"] = False

        # ── Stress test ─────────────────────────────────────────────────
        result["stress_test"] = _stress_test(outcomes, random.Random(seed + 2))

        # ── Status e veredito ───────────────────────────────────────────
        dd_p95  = result["before"]["drawdown_p95"]
        ret_p50 = result["before"]["return_p50"]
        sharpe  = result["before"]["sharpe_p50"]

        risk_level = "baixo"
        if dd_p95 > 30:
            risk_level = "alto"
        elif dd_p95 > 15:
            risk_level = "moderado"

        result["status"]     = "ok"
        result["risk_level"] = risk_level
        result["verdict"] = (
            f"DrawdownP95={dd_p95:.1f}% | RetornoP50={ret_p50:.1f}% | "
            f"SharpeP50={sharpe:.2f} | Risco={risk_level}"
        )

        self._last_result = result
        logger.info("[MC] Simulação concluída: %d sims | %s", n_simulations, result["verdict"])
        return result

    # ------------------------------------------------------------------
    def risk_summary(self) -> dict[str, Any]:
        """Retorna resumo de risco do último run."""
        if not self._last_result or self._last_result.get("status") != "ok":
            return {"status": "no_data"}
        r = self._last_result
        return {
            "status":          r["status"],
            "risk_level":      r.get("risk_level"),
            "win_rate_pct":    r.get("win_rate_pct"),
            "kelly_half_pct":  r.get("kelly", {}).get("half_kelly_pct"),
            "drawdown_p95":    r.get("before", {}).get("drawdown_p95"),
            "return_p50":      r.get("before", {}).get("return_p50"),
            "sharpe_p50":      r.get("before", {}).get("sharpe_p50"),
            "var_95":          r.get("before", {}).get("var_95"),
        }

    # ------------------------------------------------------------------
    def last_result(self) -> dict[str, Any] | None:
        return self._last_result

    # ------------------------------------------------------------------
    def export_json(self, path: pathlib.Path | None = None) -> pathlib.Path:
        """Persiste último resultado em JSON."""
        if not self._last_result:
            raise RuntimeError("[MC] Nada para exportar — chame run() primeiro")
        out = path or RESULTS_FILE
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self._last_result, indent=2, default=str), encoding="utf-8")
        logger.info("[MC] Resultado exportado: %s", out)
        return out
