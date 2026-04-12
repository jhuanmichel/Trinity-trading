"""
backtesting_engine.py — Walk-Forward Backtesting Engine (10 anos BTCUSDT)

Fluxo:
  1. Carrega dados históricos de data/historical/BTCUSDT_1h.parquet
  2. Agrega para 4h e 1d via resample
  3. Executa walk-forward sobre 4 janelas históricas (sem look-ahead bias)
  4. Calcula métricas: win rate, Sharpe, profit factor, max drawdown, equity curve
  5. Salva JSON em dashboard/backtest_results.json

Uso standalone:
    python backtesting_engine.py                 # executa backtest completo
    python backtesting_engine.py --window 2      # somente janela 2 (0-indexado)
    python backtesting_engine.py --find-optimal  # adiciona grid search de parâmetros
"""
import argparse
import json
import logging
import sys
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Garante root no sys.path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtesting_smc_adapter import (
    resample_to_higher_tfs,
    calculate_score_offline,
    calculate_entry_stop_tp,
)

log = logging.getLogger(__name__)


# ── Janelas walk-forward ──────────────────────────────────────────────────────

WALK_FORWARD_WINDOWS = [
    {
        "name":        "COVID_Crash",
        "description": "Crash COVID-19 + recuperação",
        "test_start":  "2020-01-01",
        "test_end":    "2020-12-31",
    },
    {
        "name":        "Bull_Run_2021",
        "description": "Bull run ATH BTC $69K",
        "test_start":  "2021-01-01",
        "test_end":    "2021-12-31",
    },
    {
        "name":        "Bear_LUNA_FTX",
        "description": "Bear market + colapso LUNA/FTX",
        "test_start":  "2022-01-01",
        "test_end":    "2022-12-31",
    },
    {
        "name":        "Recovery_ETF",
        "description": "Recuperação + aprovação ETF spot",
        "test_start":  "2023-01-01",
        "test_end":    "2024-12-31",
    },
]

# ── Parâmetros (grid para find_optimal_params) ────────────────────────────────

DEFAULT_PARAMS = {
    "score_threshold": 60,
    "atr_mult":        2.0,
    "tp1_ratio":       1.5,
    "tp2_ratio":       3.0,
    "tp3_ratio":       5.0,
    "step_bars":       4,      # passo entre análises: 4 × 1h = 4h
    "lookback_bars":   200,    # janela de análise SMC: 200 × 1h ≈ 8 dias
    "max_forward_bars":120,    # máximo forward: 120 × 1h = 5 dias
    "min_confluences": 4,
    "risk_pct":        1.0,    # % do capital por trade
    "initial_capital": 10_000.0,
}

PARAM_GRID = {
    "score_threshold": [55, 60, 62, 65, 68, 70],
    "atr_mult":        [1.5, 2.0, 2.5],
    "tp1_ratio":       [1.5, 2.0, 2.5],
    "tp2_ratio":       [3.0, 4.0, 5.0],
}


# ── Trade dataclass ───────────────────────────────────────────────────────────

@dataclass
class Trade:
    id:             int
    timestamp:      str          # ISO 8601
    window_name:    str
    direction:      str          # "LONG" | "SHORT"
    smc_score:      float
    confluences:    int
    entry:          float
    stop:           float
    tp1:            float
    tp2:            float
    tp3:            float
    atr:            float
    partial_exits:  list = field(default_factory=list)
    exit_reason:    str  = "TIMEOUT"
    exit_price:     float = 0.0
    pnl_r:          float = 0.0   # R múltiplo
    pnl_pct:        float = 0.0   # % do capital
    duration_bars:  int   = 0
    result:         str   = "TIMEOUT"  # "WIN" | "LOSS" | "TIMEOUT"


# ── Engine class ──────────────────────────────────────────────────────────────

class BacktestingEngine:

    DATA_DIR    = _ROOT / "data" / "historical"
    RESULTS_DIR = _ROOT / "dashboard"
    RESULT_FILE = _ROOT / "dashboard" / "backtest_results.json"

    def __init__(self, params: dict = None):
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self._df_1h: Optional[pd.DataFrame] = None
        self._dfs:   Optional[dict]          = None

    # ── Data loading ──────────────────────────────────────────────────────────

    def load_data(self) -> bool:
        """
        Carrega parquet BTCUSDT_1h e agrega para 4h/1d.

        Returns:
            True se dados carregados com sucesso, False caso contrário.
        """
        parquet = self.DATA_DIR / "BTCUSDT_1h.parquet"
        if not parquet.exists():
            log.warning(
                f"[BacktestingEngine] Parquet não encontrado: {parquet}. "
                "Execute: python scripts/fetch_historical_data.py"
            )
            return False

        try:
            df = pd.read_parquet(parquet)
            if df.empty:
                log.warning("[BacktestingEngine] Parquet vazio.")
                return False

            self._df_1h = df
            self._dfs   = resample_to_higher_tfs(df)
            log.info(
                f"[BacktestingEngine] Dados carregados: {len(df):,} candles "
                f"({df.index[0].date()} → {df.index[-1].date()})"
            )
            return True
        except Exception as e:
            log.error(f"[BacktestingEngine] Erro ao carregar parquet: {e}")
            return False

    # ── Signal simulation (zero API calls) ────────────────────────────────────

    def simulate_signal(
        self,
        window: pd.DataFrame,
        params: dict,
    ) -> Optional[dict]:
        """
        Simula análise SMC sobre janela histórica sem chamadas externas.

        Args:
            window: DataFrame OHLCV (últimas lookback_bars candles)
            params: parâmetros de threshold e ATR

        Returns:
            dict com sinal (direction, score, entry, stop, tp1, tp2, tp3)
            ou None se nenhum sinal válido.
        """
        if len(window) < 30:
            return None

        score_data = calculate_score_offline(window)

        if not score_data.get("valid"):
            return None

        direction   = score_data.get("direction", "AGUARDANDO")
        smc_score   = score_data.get("smc_score", 50.0)
        confluences = score_data.get("confluences", 0)

        if direction not in ("LONG", "SHORT"):
            return None
        if smc_score < params.get("score_threshold", 60):
            return None
        if confluences < params.get("min_confluences", 4):
            return None

        levels = calculate_entry_stop_tp(
            window,
            direction,
            atr_mult  = params.get("atr_mult",   2.0),
            tp1_ratio = params.get("tp1_ratio",  1.5),
            tp2_ratio = params.get("tp2_ratio",  3.0),
            tp3_ratio = params.get("tp3_ratio",  5.0),
        )

        if levels["risk"] <= 0:
            return None

        return {
            "direction":   direction,
            "smc_score":   smc_score,
            "confluences": confluences,
            **levels,
        }

    # ── Outcome evaluation ────────────────────────────────────────────────────

    def evaluate_outcome(
        self,
        signal: dict,
        df_forward: pd.DataFrame,
    ) -> dict:
        """
        Simula a evolução de um trade: exit parcial 1/3 em cada TP.

        Lógica de exit (priority: STOP > TP3 > TP2 > TP1):
          - LONG: candle por candle, verifica low≤stop ou high≥tp_n
          - SHORT: verifica high≥stop ou low≤tp_n
          - Stop fecha remaining_fraction ao preço stop
          - Expiry: fecha ao close do último candle (TIMEOUT)

        Args:
            signal:     dict com direction, entry, stop, tp1, tp2, tp3
            df_forward: DataFrame dos próximos max_forward_bars candles

        Returns:
            dict com exit_reason, exit_price, pnl_r, pnl_pct, partial_exits,
                  duration_bars, result
        """
        direction = signal["direction"]
        entry     = signal["entry"]
        stop      = signal["stop"]
        tp1       = signal["tp1"]
        tp2       = signal["tp2"]
        tp3       = signal["tp3"]
        risk      = abs(entry - stop)

        if risk <= 0:
            return self._timeout_result(entry, df_forward, signal)

        # Estado das saídas parciais
        tp1_hit = tp2_hit = tp3_hit = False
        remaining = 1.0   # fraction do position ainda aberta
        partial_exits = []
        is_long = direction == "LONG"

        def _pnl_r_for_exit(price: float) -> float:
            delta = (price - entry) if is_long else (entry - price)
            return round(delta / risk, 4)

        for i, (ts, row) in enumerate(df_forward.iterrows()):
            hi = float(row["high"])
            lo = float(row["low"])

            # ── Stop loss ─────────────────────────────────────────────────
            stop_hit = (lo <= stop) if is_long else (hi >= stop)
            if stop_hit and remaining > 0:
                partial_exits.append({
                    "tp": "STOP", "price": stop,
                    "fraction": remaining, "pnl_r": _pnl_r_for_exit(stop)
                })
                total_pnl_r = sum(
                    p["pnl_r"] * p["fraction"] for p in partial_exits
                )
                return {
                    "exit_reason":    "STOP",
                    "exit_price":     stop,
                    "pnl_r":          round(total_pnl_r, 4),
                    "pnl_pct":        round(total_pnl_r * signal.get("risk", risk / entry * 100), 4),
                    "partial_exits":  partial_exits,
                    "duration_bars":  i + 1,
                    "result":         "LOSS",
                }

            # ── TP3 ───────────────────────────────────────────────────────
            tp3_triggered = (hi >= tp3) if is_long else (lo <= tp3)
            if not tp3_hit and tp3_triggered and remaining > 0:
                fraction = remaining
                tp3_hit  = True
                partial_exits.append({
                    "tp": "TP3", "price": tp3,
                    "fraction": fraction, "pnl_r": _pnl_r_for_exit(tp3)
                })
                remaining -= fraction
                total_pnl_r = sum(p["pnl_r"] * p["fraction"] for p in partial_exits)
                return {
                    "exit_reason":    "TP3",
                    "exit_price":     tp3,
                    "pnl_r":          round(total_pnl_r, 4),
                    "pnl_pct":        round(total_pnl_r * self.params["risk_pct"], 4),
                    "partial_exits":  partial_exits,
                    "duration_bars":  i + 1,
                    "result":         "WIN",
                }

            # ── TP2 ───────────────────────────────────────────────────────
            tp2_triggered = (hi >= tp2) if is_long else (lo <= tp2)
            if not tp2_hit and tp2_triggered and remaining > 0:
                tp2_hit = True
                partial_exits.append({
                    "tp": "TP2", "price": tp2,
                    "fraction": 1/3, "pnl_r": _pnl_r_for_exit(tp2)
                })
                remaining -= 1/3

            # ── TP1 ───────────────────────────────────────────────────────
            tp1_triggered = (hi >= tp1) if is_long else (lo <= tp1)
            if not tp1_hit and tp1_triggered and remaining > 0:
                tp1_hit = True
                partial_exits.append({
                    "tp": "TP1", "price": tp1,
                    "fraction": 1/3, "pnl_r": _pnl_r_for_exit(tp1)
                })
                remaining -= 1/3

        # ── Timeout: fecha ao último close ────────────────────────────────
        return self._timeout_result(entry, df_forward, signal, partial_exits, remaining)

    def _timeout_result(
        self,
        entry: float,
        df_forward: pd.DataFrame,
        signal: dict,
        partial_exits: list = None,
        remaining: float = 1.0,
    ) -> dict:
        """Fecha posição ao preço de mercado atual (timeout)."""
        partial_exits = partial_exits or []
        close_price = float(df_forward["close"].iloc[-1]) if not df_forward.empty else entry
        risk        = abs(entry - signal["stop"]) if signal.get("stop") else 1.0
        is_long     = signal["direction"] == "LONG"

        delta = (close_price - entry) if is_long else (entry - close_price)
        exit_pnl_r = round(delta / risk, 4) if risk > 0 else 0.0

        if remaining > 0:
            partial_exits.append({
                "tp": "TIMEOUT", "price": close_price,
                "fraction": remaining, "pnl_r": exit_pnl_r
            })

        total_pnl_r = sum(p["pnl_r"] * p["fraction"] for p in partial_exits)
        result = "WIN" if total_pnl_r > 0 else "LOSS" if total_pnl_r < 0 else "TIMEOUT"

        return {
            "exit_reason":   "TIMEOUT",
            "exit_price":    close_price,
            "pnl_r":         round(total_pnl_r, 4),
            "pnl_pct":       round(total_pnl_r * self.params["risk_pct"], 4),
            "partial_exits": partial_exits,
            "duration_bars": len(df_forward),
            "result":        result,
        }

    # ── Walk-forward window ────────────────────────────────────────────────────

    def run_window(
        self,
        window_cfg: dict,
        params: dict,
    ) -> list[Trade]:
        """
        Executa walk-forward sobre um período de teste.

        Args:
            window_cfg: dict com test_start, test_end, name
            params:     dict com threshold, ATR, TP ratios, etc.

        Returns:
            Lista de Trade executados no período.
        """
        if self._df_1h is None:
            raise RuntimeError("Dados não carregados. Chame load_data() primeiro.")

        df_full = self._df_1h
        dfs     = self._dfs

        # Slice do período de teste
        ts_start = pd.Timestamp(window_cfg["test_start"], tz="UTC")
        ts_end   = pd.Timestamp(window_cfg["test_end"],   tz="UTC") + pd.Timedelta(days=1)

        df_test = df_full[(df_full.index >= ts_start) & (df_full.index < ts_end)]
        if len(df_test) < 50:
            log.warning(f"[{window_cfg['name']}] Poucos dados no período: {len(df_test)} candles")
            return []

        lookback    = params.get("lookback_bars",   200)
        step        = params.get("step_bars",          4)
        max_forward = params.get("max_forward_bars",  120)
        min_conf    = params.get("min_confluences",    4)

        trades:     list[Trade] = []
        trade_id    = 0
        last_exit_i = -1   # índice do último exit (cooldown simples: 1 step)

        idx_start = max(0, df_full.index.get_loc(ts_start) - lookback) if ts_start in df_full.index else 0

        # Encontra posição inicial corretamente
        try:
            abs_start = df_full.index.get_indexer([ts_start], method="bfill")[0]
        except Exception:
            abs_start = lookback

        for step_i in range(0, len(df_test) - max_forward, step):
            abs_i = abs_start + step_i

            # Janela histórica (sem look-ahead: até abs_i inclusive)
            window_start = max(0, abs_i - lookback)
            window       = df_full.iloc[window_start : abs_i + 1]

            if len(window) < 30:
                continue

            # Sem trades sobrepostos: aguarda cooldown
            if step_i < last_exit_i + step:
                continue

            signal = self.simulate_signal(window, params)
            if signal is None:
                continue

            # Forward: próximos max_forward candles após o sinal
            forward_end = min(abs_i + max_forward, len(df_full))
            df_forward  = df_full.iloc[abs_i + 1 : forward_end]

            if df_forward.empty:
                continue

            outcome = self.evaluate_outcome(signal, df_forward)

            trade_id += 1
            t = Trade(
                id             = trade_id,
                timestamp      = df_full.index[abs_i].isoformat(),
                window_name    = window_cfg["name"],
                direction      = signal["direction"],
                smc_score      = round(signal["smc_score"], 2),
                confluences    = signal["confluences"],
                entry          = round(signal["entry"], 4),
                stop           = round(signal["stop"],  4),
                tp1            = round(signal["tp1"],   4),
                tp2            = round(signal["tp2"],   4),
                tp3            = round(signal["tp3"],   4),
                atr            = round(signal.get("atr", 0.0), 4),
                partial_exits  = outcome["partial_exits"],
                exit_reason    = outcome["exit_reason"],
                exit_price     = round(outcome["exit_price"], 4),
                pnl_r          = round(outcome["pnl_r"],   4),
                pnl_pct        = round(outcome["pnl_pct"], 4),
                duration_bars  = outcome["duration_bars"],
                result         = outcome["result"],
            )
            trades.append(t)

            # Cooldown: próximo sinal após o exit deste trade
            last_exit_i = step_i + outcome["duration_bars"] // step

            log.debug(
                f"[{window_cfg['name']}] Trade {trade_id}: {t.direction} "
                f"score={t.smc_score} → {t.exit_reason} {t.pnl_r:+.2f}R"
            )

        log.info(
            f"[{window_cfg['name']}] {len(trades)} trades "
            f"({window_cfg['test_start']} → {window_cfg['test_end']})"
        )
        return trades

    # ── Metrics ───────────────────────────────────────────────────────────────

    def calculate_metrics(
        self,
        trades: list[Trade],
        initial_capital: float = 10_000.0,
    ) -> tuple[dict, list]:
        """
        Calcula métricas de performance e equity curve.

        Returns:
            (metrics_dict, equity_curve_list)
        """
        if not trades:
            empty = {
                "win_rate_pct": 0.0, "expectancy_r": 0.0, "sharpe_ratio": 0.0,
                "profit_factor": 0.0, "max_drawdown_pct": 0.0, "total_trades": 0,
                "wins": 0, "losses": 0, "total_return_pct": 0.0,
                "final_capital": initial_capital, "avg_win_r": 0.0, "avg_loss_r": 0.0,
            }
            return empty, []

        wins    = [t for t in trades if t.result == "WIN"]
        losses  = [t for t in trades if t.result == "LOSS"]
        n_total = len(trades)
        n_wins  = len(wins)
        n_losses= len(losses)

        win_rate   = n_wins / n_total * 100 if n_total > 0 else 0.0
        pnl_rs     = [t.pnl_r for t in trades]
        avg_win_r  = float(np.mean([t.pnl_r for t in wins]))   if wins   else 0.0
        avg_loss_r = float(np.mean([t.pnl_r for t in losses])) if losses else 0.0

        expectancy = (
            avg_win_r * (n_wins / n_total) + avg_loss_r * (n_losses / n_total)
        ) if n_total > 0 else 0.0

        # Sharpe annualizado (~6 trades/dia max → factor = sqrt(365*6)
        if len(pnl_rs) > 1 and np.std(pnl_rs) > 0:
            sharpe = float(np.mean(pnl_rs) / np.std(pnl_rs) * np.sqrt(365 * 6))
        else:
            sharpe = 0.0

        # Profit factor
        gross_win  = sum(t.pnl_r for t in wins   if t.pnl_r > 0)
        gross_loss = abs(sum(t.pnl_r for t in losses if t.pnl_r < 0))
        profit_factor = round(gross_win / gross_loss, 3) if gross_loss > 0 else (
            float("inf") if gross_win > 0 else 0.0
        )

        # Equity curve + max drawdown
        capital     = initial_capital
        peak        = initial_capital
        max_dd      = 0.0
        equity_pts  = []
        risk_pct    = self.params["risk_pct"] / 100.0

        for t in trades:
            trade_pnl = capital * risk_pct * t.pnl_r
            capital  += trade_pnl
            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak * 100
            if dd > max_dd:
                max_dd = dd

            date_str = t.timestamp[:10]   # "YYYY-MM-DD"
            equity_pts.append({"date": date_str, "equity": round(capital, 2)})

        total_return = (capital - initial_capital) / initial_capital * 100

        metrics = {
            "win_rate_pct":     round(win_rate,    2),
            "expectancy_r":     round(expectancy,  4),
            "sharpe_ratio":     round(sharpe,      3),
            "profit_factor":    round(profit_factor, 3) if profit_factor != float("inf") else 99.0,
            "max_drawdown_pct": round(max_dd,      2),
            "total_trades":     n_total,
            "wins":             n_wins,
            "losses":           n_losses,
            "total_return_pct": round(total_return, 2),
            "final_capital":    round(capital,     2),
            "avg_win_r":        round(avg_win_r,   4),
            "avg_loss_r":       round(avg_loss_r,  4),
        }
        return metrics, equity_pts

    # ── Full backtest ──────────────────────────────────────────────────────────

    def run_full_backtest(self, params: dict = None) -> dict:
        """
        Executa walk-forward sobre todas as 4 janelas e salva resultados.

        Args:
            params: override de parâmetros (ou usa DEFAULT_PARAMS)

        Returns:
            dict com config, metrics, equity_curve, windows, trades
        """
        if not self.load_data():
            return {
                "status":       "no_data",
                "error":        "Parquet não encontrado. Execute fetch_historical_data.py",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

        run_params     = {**self.params, **(params or {})}
        all_trades:    list[Trade] = []
        window_results: list[dict] = []

        for win_cfg in WALK_FORWARD_WINDOWS:
            log.info(
                f"[BacktestingEngine] Janela: {win_cfg['name']} "
                f"({win_cfg['test_start']} → {win_cfg['test_end']})"
            )
            trades = self.run_window(win_cfg, run_params)
            all_trades.extend(trades)

            w_metrics, w_equity = self.calculate_metrics(
                trades, initial_capital=run_params["initial_capital"]
            )
            window_results.append({
                "name":        win_cfg["name"],
                "description": win_cfg.get("description", ""),
                "period":      f"{win_cfg['test_start']} → {win_cfg['test_end']}",
                "metrics":     w_metrics,
                "equity_curve":w_equity,
            })

        # Métricas globais
        global_metrics, global_equity = self.calculate_metrics(
            all_trades, initial_capital=run_params["initial_capital"]
        )

        # Serializa trades
        trades_serial = [asdict(t) for t in all_trades]

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status":       "ok",
            "config": {
                "period_days":          (
                    pd.Timestamp(WALK_FORWARD_WINDOWS[-1]["test_end"]) -
                    pd.Timestamp(WALK_FORWARD_WINDOWS[0]["test_start"])
                ).days,
                "risk_per_trade_pct":   run_params["risk_pct"],
                "initial_capital":      run_params["initial_capital"],
                "exit_rule":            "partial_thirds",
                "score_threshold":      run_params["score_threshold"],
                "atr_mult":             run_params["atr_mult"],
                "tp1_ratio":            run_params["tp1_ratio"],
                "tp2_ratio":            run_params["tp2_ratio"],
                "tp3_ratio":            run_params["tp3_ratio"],
                "step_bars":            run_params["step_bars"],
                "lookback_bars":        run_params["lookback_bars"],
                "windows":              [w["name"] for w in WALK_FORWARD_WINDOWS],
            },
            "metrics":      global_metrics,
            "equity_curve": global_equity,
            "windows":      window_results,
            "trades":       trades_serial,
        }

        # Salva JSON
        self._save_results(result)
        self._print_summary(result)
        return result

    # ── Find optimal params ───────────────────────────────────────────────────

    def find_optimal_params(self) -> dict:
        """
        Grid search sobre os parâmetros para maximizar win_rate × profit_factor.

        Usa os dados combinados de todos os períodos.
        NÃO usa os resultados do test — treina sobre todo o período disponível.

        Returns:
            dict com os melhores parâmetros encontrados + métricas esperadas.
        """
        if self._df_1h is None and not self.load_data():
            return {}

        log.info("[BacktestingEngine] Iniciando grid search de parâmetros...")

        best_score  = -1.0
        best_params = {}
        best_metrics= {}
        tested      = 0

        # Usa apenas o primeiro ano disponível como "treino" para ser rápido
        df_all = self._df_1h
        if df_all is None or df_all.empty:
            return {}

        # Periodo de treino: primeiro 18 meses disponíveis
        train_end = df_all.index[0] + pd.Timedelta(days=548)
        train_cfg = {
            "name":       "grid_search_train",
            "test_start": str(df_all.index[0].date()),
            "test_end":   str(min(train_end, df_all.index[-1]).date()),
        }

        from itertools import product as iproduct
        keys   = list(PARAM_GRID.keys())
        values = [PARAM_GRID[k] for k in keys]

        for combo in iproduct(*values):
            params = {**self.params}
            for k, v in zip(keys, combo):
                params[k] = v

            trades = self.run_window(train_cfg, params)
            if len(trades) < 10:
                continue

            metrics, _ = self.calculate_metrics(
                trades, initial_capital=params["initial_capital"]
            )
            # Score combinado: win_rate * profit_factor / max(1, drawdown_penalty)
            score = (
                metrics["win_rate_pct"] / 100.0
                * min(metrics["profit_factor"], 5.0)
                / max(1.0, metrics["max_drawdown_pct"] / 10.0)
            )
            tested += 1

            if score > best_score:
                best_score   = score
                best_params  = {k: v for k, v in zip(keys, combo)}
                best_metrics = metrics

            log.debug(
                f"  [{tested}] {dict(zip(keys, combo))} → "
                f"wr={metrics['win_rate_pct']:.1f}% pf={metrics['profit_factor']:.2f} "
                f"score={score:.3f}"
            )

        log.info(
            f"[BacktestingEngine] Grid search: {tested} combos testados. "
            f"Melhores parâmetros: {best_params} "
            f"(win_rate={best_metrics.get('win_rate_pct',0):.1f}%)"
        )

        return {
            "optimal_params":  {**self.params, **best_params},
            "expected_metrics": best_metrics,
            "combos_tested":   tested,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _save_results(self, result: dict) -> None:
        """Salva JSON de resultados em dashboard/backtest_results.json."""
        try:
            self.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            self.RESULT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
            log.info(f"[BacktestingEngine] Resultados salvos: {self.RESULT_FILE}")
        except Exception as e:
            log.error(f"[BacktestingEngine] Falha ao salvar resultados: {e}")

    def _print_summary(self, result: dict) -> None:
        """Imprime resumo no log."""
        m = result.get("metrics", {})
        log.info("=" * 55)
        log.info("  BACKTEST WALK-FORWARD — RESUMO")
        log.info("=" * 55)
        log.info(f"  Win Rate:       {m.get('win_rate_pct', 0):.1f}%")
        log.info(f"  Profit Factor:  {m.get('profit_factor', 0):.2f}")
        log.info(f"  Sharpe Ratio:   {m.get('sharpe_ratio', 0):.3f}")
        log.info(f"  Max Drawdown:   {m.get('max_drawdown_pct', 0):.1f}%")
        log.info(f"  Total Return:   {m.get('total_return_pct', 0):+.1f}%")
        log.info(f"  Trades:         {m.get('total_trades', 0)} "
                 f"({m.get('wins', 0)}W / {m.get('losses', 0)}L)")
        log.info(f"  Capital Final:  ${m.get('final_capital', 0):,.2f}")
        log.info("=" * 55)
        for win in result.get("windows", []):
            wm = win.get("metrics", {})
            log.info(
                f"  {win['name']:<20} "
                f"WR={wm.get('win_rate_pct',0):.0f}% "
                f"PF={wm.get('profit_factor',0):.1f} "
                f"DD={wm.get('max_drawdown_pct',0):.0f}% "
                f"({wm.get('total_trades',0)} trades)"
            )
        log.info("=" * 55)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _run_cli():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Trinity Walk-Forward Backtesting Engine")
    parser.add_argument("--window",       type=int, default=-1,
                        help="Executar somente a janela N (0-3)")
    parser.add_argument("--find-optimal", action="store_true",
                        help="Executar grid search após backtest")
    parser.add_argument("--threshold",    type=float, default=DEFAULT_PARAMS["score_threshold"],
                        help=f"Score threshold (default: {DEFAULT_PARAMS['score_threshold']})")
    args = parser.parse_args()

    engine = BacktestingEngine()

    if not engine.load_data():
        print(
            "\nERRO: Dados históricos não encontrados.\n"
            "Execute: python scripts/fetch_historical_data.py\n"
        )
        sys.exit(1)

    if args.window >= 0:
        if args.window >= len(WALK_FORWARD_WINDOWS):
            print(f"Janela {args.window} inválida. Escolha 0-{len(WALK_FORWARD_WINDOWS)-1}.")
            sys.exit(1)
        win_cfg = WALK_FORWARD_WINDOWS[args.window]
        params  = {**DEFAULT_PARAMS, "score_threshold": args.threshold}
        trades  = engine.run_window(win_cfg, params)
        metrics, equity = engine.calculate_metrics(trades)
        engine._print_summary({"metrics": metrics, "windows": [{
            "name": win_cfg["name"], "metrics": metrics
        }]})
    else:
        params = {**DEFAULT_PARAMS, "score_threshold": args.threshold}
        result = engine.run_full_backtest(params)

        if args.find_optimal:
            opt = engine.find_optimal_params()
            if opt:
                result["optimal_params"] = opt
                engine._save_results(result)
                log.info(f"Parâmetros ótimos: {opt['optimal_params']}")


if __name__ == "__main__":
    _run_cli()
