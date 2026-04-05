"""
backtester/performance_metrics.py — Métricas de performance para o backtest.

Calcula win rate, expectância, Sharpe, max drawdown, profit factor e equity curve.
"""
import math
import logging
from datetime import date

log = logging.getLogger(__name__)


def calc_metrics(trades: list, initial_capital: float = 10_000.0) -> dict:
    """
    Calcula métricas de performance a partir da lista de Trade dataclasses.

    Returns:
        dict com:
            metrics         — 12 métricas numéricas
            equity_curve    — lista [{date, equity}]
            trades_serial   — lista serializável de trades (para JSON)
    """
    if not trades:
        return {
            "metrics": _empty_metrics(initial_capital),
            "equity_curve": [{"date": str(date.today()), "equity": initial_capital}],
            "trades_serial": [],
        }

    wins   = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    total  = len(trades)

    win_rate  = len(wins) / total if total else 0.0
    loss_rate = 1.0 - win_rate

    avg_win_r  = sum(t.pnl_r for t in wins)  / len(wins)  if wins   else 0.0
    avg_loss_r = sum(t.pnl_r for t in losses) / len(losses) if losses else 0.0

    expectancy_r = round(win_rate * avg_win_r + loss_rate * avg_loss_r, 4)

    # Equity curve — 1% de risco por trade, capital acumulado
    equity    = initial_capital
    eq_curve  = []
    peak      = initial_capital
    max_dd    = 0.0

    for t in trades:
        equity += equity * (t.pnl_pct / 100.0)
        equity  = round(equity, 2)
        peak    = max(peak, equity)
        dd      = (equity - peak) / peak * 100
        max_dd  = min(max_dd, dd)
        eq_curve.append({"date": str(t.timestamp.date()), "equity": equity})

    total_return_pct = round((equity - initial_capital) / initial_capital * 100, 2)

    # Sharpe — annualizado por trade
    # Considera ~252 dias de trading por ano, ~1.5 trades/dia médio
    pnl_rs = [t.pnl_r for t in trades]
    mean_r = sum(pnl_rs) / len(pnl_rs)
    var_r  = sum((r - mean_r) ** 2 for r in pnl_rs) / len(pnl_rs) if len(pnl_rs) > 1 else 0.0
    std_r  = math.sqrt(var_r)

    # Estima frequência de trades por ano para anualizar
    if len(trades) >= 2:
        span_days = max(1, (trades[-1].timestamp - trades[0].timestamp).days)
        trades_per_year = (len(trades) / span_days) * 252
    else:
        trades_per_year = 252

    sharpe = round((mean_r / std_r) * math.sqrt(trades_per_year), 2) if std_r > 0 else 0.0

    # Profit factor
    gross_profit = sum(t.pnl_pct for t in wins)
    gross_loss   = abs(sum(t.pnl_pct for t in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")

    metrics = {
        "total_trades":       total,
        "wins":               len(wins),
        "losses":             len(losses),
        "win_rate_pct":       round(win_rate * 100, 1),
        "expectancy_r":       expectancy_r,
        "sharpe_ratio":       sharpe,
        "profit_factor":      profit_factor if profit_factor != float("inf") else 99.0,
        "max_drawdown_pct":   round(max_dd, 2),
        "total_return_pct":   total_return_pct,
        "final_capital":      round(equity, 2),
        "initial_capital":    initial_capital,
        "avg_win_r":          round(avg_win_r, 2),
        "avg_loss_r":         round(avg_loss_r, 2),
    }

    # Serializa trades para JSON
    trades_serial = []
    for t in trades:
        trades_serial.append({
            "id":            t.id,
            "timestamp":     str(t.timestamp),
            "direction":     t.direction,
            "smc_score":     t.smc_score,
            "confluences":   t.confluences,
            "alignment":     t.alignment,
            "entry":         t.entry,
            "stop":          t.stop,
            "tp1":           t.tp1,
            "tp2":           t.tp2,
            "tp3":           t.tp3,
            "rr1":           t.rr1,
            "rr2":           t.rr2,
            "rr3":           t.rr3,
            "partial_exits": [
                {
                    "tp":       pe.tp,
                    "price":    pe.price,
                    "fraction": round(pe.fraction, 4),
                    "pnl_r":    pe.pnl_r,
                }
                for pe in t.partial_exits
            ],
            "exit_reason":   t.exit_reason,
            "exit_price":    t.exit_price,
            "pnl_r":         t.pnl_r,
            "pnl_pct":       t.pnl_pct,
            "duration_bars": t.duration_bars,
            "result":        t.result,
        })

    log.info(
        f"[metrics] Trades={total} | WR={metrics['win_rate_pct']}% | "
        f"E={expectancy_r:+.2f}R | Sharpe={sharpe} | "
        f"DD={max_dd:.1f}% | Ret={total_return_pct:+.1f}%"
    )

    return {
        "metrics":       metrics,
        "equity_curve":  eq_curve,
        "trades_serial": trades_serial,
    }


def _empty_metrics(initial_capital: float) -> dict:
    return {
        "total_trades":    0,
        "wins":            0,
        "losses":          0,
        "win_rate_pct":    0.0,
        "expectancy_r":    0.0,
        "sharpe_ratio":    0.0,
        "profit_factor":   0.0,
        "max_drawdown_pct": 0.0,
        "total_return_pct": 0.0,
        "final_capital":   initial_capital,
        "initial_capital": initial_capital,
        "avg_win_r":       0.0,
        "avg_loss_r":      0.0,
    }
