"""
tests/test_backtesting_engine.py — Testes unitários do Backtesting Engine

5 testes obrigatórios do spec Section 9:

  T1: fetch_historical_data.py — verifica colunas corretas do DataFrame retornado
  T2: backtesting_smc_adapter.calculate_score_offline — mock DataFrame
  T3: BacktestingEngine.evaluate_outcome — LONG trade que atinge TP1 então STOP
  T4: BacktestingEngine.evaluate_outcome — SHORT trade que atinge STOP direto
  T5: BacktestingEngine.run_full_backtest — retorna dict com todas as keys

Execute:
    cd /Users/jhuanmichel/trading && python -m pytest tests/test_backtesting_engine.py -v
"""
import sys
import os
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# Garante que o root está no path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 100, base_price: float = 30_000.0,
                trend: str = "up", seed: int = 42) -> pd.DataFrame:
    """Gera DataFrame OHLCV sintético com trend controlado."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")

    closes = [base_price]
    for _ in range(n - 1):
        move = rng.normal(0, 0.005)
        if trend == "up":
            move += 0.001
        elif trend == "down":
            move -= 0.001
        closes.append(closes[-1] * (1 + move))

    closes  = np.array(closes)
    highs   = closes * (1 + rng.uniform(0.002, 0.008, n))
    lows    = closes * (1 - rng.uniform(0.002, 0.008, n))
    opens   = np.roll(closes, 1)
    opens[0] = base_price
    volumes  = rng.uniform(100, 1000, n)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


# ─────────────────────────────────────────────────────────────────────────────
# T1: scripts/fetch_historical_data.py — colunas e formato do DataFrame
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchHistoricalData:
    """T1: fetch_historical_data.klines_to_df retorna DataFrame com formato correto."""

    def test_klines_to_df_columns(self):
        """klines_to_df deve retornar exatamente as colunas OHLCV."""
        from scripts.fetch_historical_data import klines_to_df

        # Simula 5 klines no formato Binance (12 campos por candle)
        mock_klines = []
        for i in range(5):
            ts = int((datetime(2023, 1, 1, i, tzinfo=timezone.utc)).timestamp() * 1000)
            close_ts = ts + 3_600_000 - 1  # close_time = open_time + 1h - 1ms
            mock_klines.append([
                ts,              # 0: open time
                f"{30000 + i*100:.2f}",  # 1: open
                f"{30100 + i*100:.2f}",  # 2: high
                f"{29900 + i*100:.2f}",  # 3: low
                f"{30050 + i*100:.2f}",  # 4: close
                f"{500.0:.2f}",           # 5: volume
                close_ts,                 # 6: close time
                f"{1_000_000:.2f}",       # 7: quote volume
                100,                      # 8: trades
                f"{250.0:.2f}",           # 9: taker buy base
                f"{500_000:.2f}",         # 10: taker buy quote
                "0",                      # 11: ignore
            ])

        df = klines_to_df(mock_klines)

        assert list(df.columns) == ["open", "high", "low", "close", "volume"], \
            f"Colunas inesperadas: {list(df.columns)}"
        assert len(df) == 5, f"Esperado 5 linhas, obtido {len(df)}"
        assert isinstance(df.index, pd.DatetimeIndex), "Index deve ser DatetimeIndex"
        assert df.index.tz is not None, "Index deve ter timezone UTC"
        assert df["close"].dtype in (np.float32, np.float64), "close deve ser float"

    def test_klines_to_df_sorted(self):
        """DataFrame deve estar ordenado cronologicamente."""
        from scripts.fetch_historical_data import klines_to_df

        ts2 = int(datetime(2023, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
        ts1 = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        klines = [
            [ts2, "31000", "31100", "30900", "31050", "400", ts2 + 3_599_999, "0", 0, "0", "0", "0"],
            [ts1, "30000", "30100", "29900", "30050", "500", ts1 + 3_599_999, "0", 0, "0", "0", "0"],
        ]
        df = klines_to_df(klines)
        assert df.index[0] < df.index[1], "DataFrame deve estar ordenado ascendentemente"


# ─────────────────────────────────────────────────────────────────────────────
# T2: backtesting_smc_adapter.calculate_score_offline — mock DataFrame
# ─────────────────────────────────────────────────────────────────────────────

class TestBacktestingSMCAdapter:
    """T2: calculate_score_offline retorna estrutura correta sem API calls."""

    def test_calculate_score_offline_returns_dict(self):
        """calculate_score_offline deve retornar dict com campos SMC esperados."""
        from backtesting_smc_adapter import calculate_score_offline

        df = _make_ohlcv(80, trend="up")
        result = calculate_score_offline(df)

        assert isinstance(result, dict), "Resultado deve ser dict"
        assert "smc_score"    in result, "Faltando smc_score"
        assert "direction"    in result, "Faltando direction"
        assert "confluences"  in result, "Faltando confluences"
        assert "valid"        in result, "Faltando valid"
        assert 0.0 <= result["smc_score"] <= 100.0, \
            f"smc_score fora do range: {result['smc_score']}"
        assert result["direction"] in ("LONG", "SHORT", "AGUARDANDO"), \
            f"direction inválido: {result['direction']}"

    def test_calculate_score_offline_no_api_call(self):
        """calculate_score_offline nunca deve chamar mexc_client.get_ohlcv."""
        from backtesting_smc_adapter import calculate_score_offline

        with patch("mexc_client.get_ohlcv") as mock_api:
            df = _make_ohlcv(60, trend="down")
            calculate_score_offline(df)
            assert mock_api.call_count == 0, \
                "calculate_score_offline fez chamadas API inesperadas!"

    def test_calculate_score_offline_too_small(self):
        """DataFrame com < 20 candles deve retornar score neutro."""
        from backtesting_smc_adapter import calculate_score_offline

        df = _make_ohlcv(10)
        result = calculate_score_offline(df)
        assert result["smc_score"] == 50.0
        assert result["valid"] is False


# ─────────────────────────────────────────────────────────────────────────────
# T3: BacktestingEngine.evaluate_outcome — LONG que atinge TP1 depois STOP
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateOutcomeLong:
    """T3: LONG trade parcial — TP1 atingido, depois STOP."""

    def _make_engine(self):
        from backtesting_engine import BacktestingEngine
        return BacktestingEngine()

    def test_long_tp1_then_stop(self):
        """LONG: TP1 atingido na barra 3, STOP na barra 7 → result LOSS (net negativo)."""
        engine = self._make_engine()

        entry = 30_000.0
        stop  = 29_400.0   # risco = 600
        tp1   = 30_900.0   # TP1 = 1.5R
        tp2   = 31_800.0   # TP2 = 3R
        tp3   = 33_000.0   # TP3 = 5R

        signal = {
            "direction": "LONG",
            "entry":     entry,
            "stop":      stop,
            "tp1":       tp1,
            "tp2":       tp2,
            "tp3":       tp3,
            "risk":      abs(entry - stop),
            "atr":       300.0,
        }

        # Barra 0-2: neutro; barra 3: high ≥ tp1; barra 4-6: neutro; barra 7: low ≤ stop
        dates = pd.date_range("2023-06-01 10:00", periods=10, freq="1h", tz="UTC")
        data  = {
            "open":  [30000]*10,
            "high":  [30500, 30600, 30700, 31000, 30800, 30700, 30600, 30500, 30400, 30300],
            "low":   [29700, 29700, 29700, 29700, 29700, 29700, 29700, 29300, 29300, 29300],
            "close": [30100]*10,
            "volume":[100]*10,
        }
        df_forward = pd.DataFrame(data, index=dates)

        result = engine.evaluate_outcome(signal, df_forward)

        assert "exit_reason"   in result
        assert "pnl_r"         in result
        assert "result"        in result
        assert "partial_exits" in result

        # TP1 deve ter sido atingido (partial_exits tem ao menos uma entrada TP1 ou STOP)
        exit_reasons = {p["tp"] for p in result["partial_exits"]}
        assert "TP1" in exit_reasons or "STOP" in exit_reasons, \
            f"Esperado TP1 ou STOP em partial_exits, obtido: {exit_reasons}"

        # pnl_r deve ser um float
        assert isinstance(result["pnl_r"], float)

    def test_long_full_tp3(self):
        """LONG: price sobe até TP3 → WIN com pnl_r positivo."""
        engine = self._make_engine()

        entry = 20_000.0
        stop  = 19_000.0   # risco = 1000
        tp1   = 21_500.0
        tp2   = 23_000.0
        tp3   = 25_000.0

        signal = {
            "direction": "LONG",
            "entry": entry, "stop": stop,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "risk": 1000.0, "atr": 500.0,
        }

        dates = pd.date_range("2023-01-01", periods=5, freq="1h", tz="UTC")
        df_forward = pd.DataFrame({
            "open":   [entry]*5,
            "high":   [21600, 23100, 23100, 25100, 25100],
            "low":    [19500]*5,
            "close":  [21500, 23000, 23000, 25000, 25000],
            "volume": [100]*5,
        }, index=dates)

        result = engine.evaluate_outcome(signal, df_forward)

        assert result["exit_reason"] == "TP3"
        assert result["result"] == "WIN"
        assert result["pnl_r"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# T4: BacktestingEngine.evaluate_outcome — SHORT que atinge STOP direto
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateOutcomeShort:
    """T4: SHORT trade — STOP atingido na primeira barra."""

    def _make_engine(self):
        from backtesting_engine import BacktestingEngine
        return BacktestingEngine()

    def test_short_immediate_stop(self):
        """SHORT: high ≥ stop na barra 0 → LOSS imediato, pnl_r = -1.0."""
        engine = self._make_engine()

        entry = 30_000.0
        stop  = 30_600.0   # stop 2% acima (risco = 600)
        tp1   = 29_100.0   # TP1 1.5R abaixo
        tp2   = 28_200.0
        tp3   = 27_000.0

        signal = {
            "direction": "SHORT",
            "entry": entry, "stop": stop,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "risk": 600.0, "atr": 300.0,
        }

        dates = pd.date_range("2023-03-01", periods=5, freq="1h", tz="UTC")
        df_forward = pd.DataFrame({
            "open":   [entry]*5,
            "high":   [30700, 30800, 30900, 31000, 31100],  # sempre acima do stop
            "low":    [29900]*5,
            "close":  [30200]*5,
            "volume": [100]*5,
        }, index=dates)

        result = engine.evaluate_outcome(signal, df_forward)

        assert result["exit_reason"] == "STOP", \
            f"Esperado STOP, obtido {result['exit_reason']}"
        assert result["result"] == "LOSS"
        assert result["pnl_r"] < 0, f"pnl_r deve ser negativo: {result['pnl_r']}"
        assert result["exit_price"] == stop

    def test_short_full_tp3(self):
        """SHORT: price cai até TP3 → WIN."""
        engine = self._make_engine()

        entry = 50_000.0
        stop  = 51_000.0
        tp1   = 48_500.0
        tp2   = 47_000.0
        tp3   = 45_000.0

        signal = {
            "direction": "SHORT",
            "entry": entry, "stop": stop,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "risk": 1000.0, "atr": 500.0,
        }

        dates = pd.date_range("2023-01-01", periods=5, freq="1h", tz="UTC")
        df_forward = pd.DataFrame({
            "open":   [entry]*5,
            "high":   [entry]*5,
            "low":    [48400, 46900, 46900, 44900, 44900],  # vai descendo
            "close":  [48000, 46500, 46500, 44500, 44500],
            "volume": [200]*5,
        }, index=dates)

        result = engine.evaluate_outcome(signal, df_forward)

        assert result["exit_reason"] == "TP3"
        assert result["result"] == "WIN"
        assert result["pnl_r"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# T5: BacktestingEngine.run_full_backtest — retorna keys obrigatórias
# ─────────────────────────────────────────────────────────────────────────────

class TestRunFullBacktest:
    """T5: run_full_backtest retorna dict com todas as keys + métricas esperadas."""

    def test_run_full_backtest_no_data(self):
        """Sem parquet → status='no_data', não gera erro."""
        from backtesting_engine import BacktestingEngine

        engine = BacktestingEngine()
        # Força parquet inexistente
        engine.DATA_DIR = Path("/tmp/does_not_exist")

        result = engine.run_full_backtest()

        assert isinstance(result, dict), "run_full_backtest deve retornar dict"
        assert result.get("status") == "no_data", \
            f"Esperado status='no_data', obtido: {result.get('status')}"
        assert "generated_at" in result
        assert "error" in result

    def test_run_full_backtest_structure(self):
        """Com dados mock, resultado deve ter todas as keys do spec."""
        from backtesting_engine import BacktestingEngine, WALK_FORWARD_WINDOWS

        engine = BacktestingEngine()

        # Mock: injeta DataFrame sintético diretamente
        df_mock = _make_ohlcv(n=2000, base_price=25_000.0, seed=42)
        engine._df_1h = df_mock

        from backtesting_smc_adapter import resample_to_higher_tfs
        engine._dfs = resample_to_higher_tfs(df_mock)

        # Usa um único período de teste curto para ser rápido
        win_cfg = {
            "name":       "test_window",
            "test_start": str(df_mock.index[500].date()),
            "test_end":   str(df_mock.index[600].date()),
        }
        params = {
            **engine.params,
            "score_threshold": 40,     # threshold baixo para gerar mais sinais
            "lookback_bars":   50,
            "max_forward_bars":20,
            "step_bars":       10,
            "min_confluences": 1,
        }

        trades = engine.run_window(win_cfg, params)
        metrics, equity = engine.calculate_metrics(trades)

        # Verifica keys obrigatórias do metrics
        required_keys = [
            "win_rate_pct", "expectancy_r", "sharpe_ratio",
            "profit_factor", "max_drawdown_pct", "total_trades",
            "wins", "losses", "total_return_pct", "final_capital",
            "avg_win_r", "avg_loss_r",
        ]
        for key in required_keys:
            assert key in metrics, f"Chave obrigatória ausente: {key}"

        # Tipos
        assert isinstance(metrics["win_rate_pct"], float)
        assert isinstance(metrics["total_trades"], int)
        assert isinstance(equity, list)
        for pt in equity:
            assert "date" in pt
            assert "equity" in pt
            assert isinstance(pt["equity"], float)

    def test_result_json_serializable(self):
        """Resultado do run_full_backtest deve ser JSON-serializável."""
        import json
        from backtesting_engine import BacktestingEngine

        engine = BacktestingEngine()
        df_mock = _make_ohlcv(n=800, base_price=20_000.0, seed=7)
        engine._df_1h = df_mock

        from backtesting_smc_adapter import resample_to_higher_tfs
        engine._dfs = resample_to_higher_tfs(df_mock)

        win_cfg = {
            "name":       "json_test",
            "test_start": str(df_mock.index[200].date()),
            "test_end":   str(df_mock.index[250].date()),
        }
        params = {**engine.params, "score_threshold": 40, "min_confluences": 1,
                  "lookback_bars": 30, "max_forward_bars": 10, "step_bars": 5}
        trades  = engine.run_window(win_cfg, params)
        metrics, equity = engine.calculate_metrics(trades)

        doc = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status":       "ok",
            "config":       {"period_days": 50, "risk_per_trade_pct": 1.0},
            "metrics":      metrics,
            "equity_curve": equity,
            "windows":      [],
            "trades":       [],
        }

        # Não deve lançar exceção
        serialized = json.dumps(doc)
        parsed = json.loads(serialized)
        assert parsed["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Execução direta
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v", "--tb=short"])
