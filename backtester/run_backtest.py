"""
backtester/run_backtest.py — Ponto de entrada para executar backtest.

Lógica de seleção automática de engine:
  1. Se data/historical/BTCUSDT_1h.parquet existir → usa BacktestingEngine
     (walk-forward 10 anos, 4 janelas históricas, sem API)
  2. Caso contrário → usa pipeline legado MEXC/CCXT
     (até 180 dias de dados recentes via exchange)

Uso:
    python -m backtester.run_backtest
    python -m backtester.run_backtest --days 90 --symbol BTC/USDT:USDT --threshold 55
    python -m backtester.run_backtest --force-legacy   # força pipeline MEXC mesmo com parquet
"""
import argparse
import json
import logging
import sys
import os
from datetime import datetime
from pathlib import Path

# Garante que o root do projeto está no path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt = "%H:%M:%S",
)
log = logging.getLogger("run_backtest")

BACKTEST_FILE  = ROOT / "dashboard" / "backtest_results.json"
PARQUET_FILE   = ROOT / "data" / "historical" / "BTCUSDT_1h.parquet"


# ── Engine selection ──────────────────────────────────────────────────────────

def _use_historical_engine() -> bool:
    """Retorna True se o parquet histórico existe e tem dados suficientes."""
    if not PARQUET_FILE.exists():
        return False
    try:
        import pandas as pd
        df = pd.read_parquet(PARQUET_FILE)
        return len(df) > 1000
    except Exception:
        return False


def run_historical(params: dict = None) -> dict:
    """
    Executa backtest walk-forward 10 anos com BacktestingEngine.
    Usa data/historical/BTCUSDT_1h.parquet (sem chamadas externas).
    """
    from backtesting_engine import BacktestingEngine

    engine = BacktestingEngine(params)
    result = engine.run_full_backtest()

    log.info(
        f"[run_backtest] Engine histórico concluído — "
        f"{result.get('metrics', {}).get('total_trades', 0)} trades"
    )
    return result


def run_legacy(symbol: str = "BTC/USDT:USDT", days: int = 180,
               threshold: float = 60.0, min_confluences: int = 5,
               initial_capital: float = 10_000.0) -> dict:
    """
    Pipeline legado: busca dados MEXC via CCXT e executa walk-forward recente.
    Mantido como fallback quando dados históricos não estão disponíveis.
    """
    from backtester.data_fetcher        import fetch_all_timeframes
    from backtester.trade_simulator     import run_walkforward
    from backtester.performance_metrics import calc_metrics

    log.info(
        f"=== BACKTEST LEGACY: {symbol} | {days} dias | "
        f"threshold={threshold} | min_conf={min_confluences} ==="
    )

    log.info("Etapa 1/3: Buscando dados históricos...")
    dfs = fetch_all_timeframes(symbol=symbol, days=days)
    for tf, df in dfs.items():
        log.info(f"  {tf}: {len(df)} candles")

    log.info("Etapa 2/3: Walk-forward signal simulation...")
    trades = run_walkforward(
        dfs=dfs, symbol=symbol,
        threshold=threshold, min_confluences=min_confluences,
    )
    log.info(f"  Total de trades gerados: {len(trades)}")

    log.info("Etapa 3/3: Calculando métricas de performance...")
    result = calc_metrics(trades, initial_capital=initial_capital)

    doc = {
        "generated_at": datetime.utcnow().isoformat(),
        "status":       "ok",
        "config": {
            "symbol":             symbol,
            "period_days":        days,
            "risk_per_trade_pct": 1.0,
            "exit_rule":          "partial_thirds",
            "score_threshold":    threshold,
            "min_confluences":    min_confluences,
            "initial_capital":    initial_capital,
            "engine":             "legacy_mexc",
        },
        "metrics":      result["metrics"],
        "equity_curve": result["equity_curve"],
        "windows":      [],
        "trades":       result["trades_serial"],
    }

    BACKTEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    BACKTEST_FILE.write_text(json.dumps(doc, indent=2, default=str))
    log.info(f"Resultados salvos em {BACKTEST_FILE}")

    m = result["metrics"]
    log.info(
        f"\n{'='*55}\n"
        f"  Trades:        {m['total_trades']} (W:{m['wins']} L:{m['losses']})\n"
        f"  Win Rate:      {m['win_rate_pct']}%\n"
        f"  Expectância:   {m['expectancy_r']:+.3f}R\n"
        f"  Sharpe:        {m['sharpe_ratio']}\n"
        f"  Max DD:        {m['max_drawdown_pct']:.1f}%\n"
        f"  Retorno Total: {m['total_return_pct']:+.1f}%\n"
        f"  Capital Final: ${m['final_capital']:,.2f}\n"
        f"{'='*55}"
    )
    return doc


def run(symbol: str = "BTC/USDT:USDT", days: int = 180, threshold: float = 60.0,
        min_confluences: int = 5, initial_capital: float = 10_000.0,
        force_legacy: bool = False) -> dict:
    """
    Ponto de entrada principal — seleciona engine automaticamente.

    Args:
        force_legacy: força o pipeline MEXC/CCXT mesmo que parquet exista
    """
    if not force_legacy and _use_historical_engine():
        log.info(
            "[run_backtest] Dados históricos detectados → usando BacktestingEngine "
            "(walk-forward 10 anos)"
        )
        return run_historical()
    else:
        if not force_legacy:
            log.info(
                "[run_backtest] Parquet não encontrado → usando engine legado MEXC/CCXT. "
                "Para dados completos: python scripts/fetch_historical_data.py"
            )
        return run_legacy(
            symbol=symbol, days=days, threshold=threshold,
            min_confluences=min_confluences, initial_capital=initial_capital,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Trinity Trading Backtest Engine",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol",          default="BTC/USDT:USDT",
                        help="Par para backtesting (legado)")
    parser.add_argument("--days",            type=int,   default=180,
                        help="Período em dias (legado)")
    parser.add_argument("--threshold",       type=float, default=60.0,
                        help="Score threshold mínimo (legado)")
    parser.add_argument("--min-confluences", type=int,   default=5,
                        help="Mínimo de confluências SMC (legado)")
    parser.add_argument("--capital",         type=float, default=10_000.0,
                        help="Capital inicial")
    parser.add_argument("--force-legacy",    action="store_true",
                        help="Força pipeline MEXC/CCXT mesmo com parquet disponível")
    args = parser.parse_args()

    run(
        symbol          = args.symbol,
        days            = args.days,
        threshold       = args.threshold,
        min_confluences = args.min_confluences,
        initial_capital = args.capital,
        force_legacy    = args.force_legacy,
    )


if __name__ == "__main__":
    main()
