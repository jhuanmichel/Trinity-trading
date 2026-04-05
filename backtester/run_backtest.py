"""
backtester/run_backtest.py — CLI para executar o backtest e salvar resultados.

Uso:
    python -m backtester.run_backtest
    python -m backtester.run_backtest --days 90 --symbol BTC/USDT:USDT --threshold 55
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

BACKTEST_FILE = ROOT / "dashboard" / "backtest_results.json"


def run(symbol: str = "BTC/USDT:USDT", days: int = 180, threshold: float = 55.0,
        initial_capital: float = 10_000.0) -> dict:
    """
    Executa o pipeline completo de backtest e retorna o dict de resultados.

    Pipeline:
        1. fetch_all_timeframes()  — busca histórico OHLCV paginado
        2. run_walkforward()       — gera sinais e simula exits
        3. calc_metrics()          — calcula métricas e equity curve
    """
    from backtester.data_fetcher    import fetch_all_timeframes
    from backtester.trade_simulator  import run_walkforward
    from backtester.performance_metrics import calc_metrics

    log.info(f"=== BACKTEST START: {symbol} | {days} dias | threshold={threshold} ===")

    # 1. Dados históricos
    log.info("Etapa 1/3: Buscando dados históricos ...")
    dfs = fetch_all_timeframes(symbol=symbol, days=days)
    for tf, df in dfs.items():
        log.info(f"  {tf}: {len(df)} candles")

    # 2. Walk-forward
    log.info("Etapa 2/3: Walk-forward signal simulation ...")
    trades = run_walkforward(dfs=dfs, symbol=symbol, threshold=threshold)
    log.info(f"  Total de trades gerados: {len(trades)}")

    # 3. Métricas
    log.info("Etapa 3/3: Calculando métricas de performance ...")
    result = calc_metrics(trades, initial_capital=initial_capital)

    # Monta documento final
    doc = {
        "generated_at": datetime.utcnow().isoformat(),
        "config": {
            "symbol":           symbol,
            "period_days":      days,
            "risk_per_trade_pct": 1.0,
            "exit_rule":        "partial_thirds",
            "signal_threshold": threshold,
            "initial_capital":  initial_capital,
        },
        "metrics":      result["metrics"],
        "equity_curve": result["equity_curve"],
        "trades":       result["trades_serial"],
    }

    # Salva JSON
    BACKTEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    BACKTEST_FILE.write_text(json.dumps(doc, indent=2, default=str))
    log.info(f"Resultados salvos em {BACKTEST_FILE}")

    m = result["metrics"]
    log.info(
        f"\n{'='*55}\n"
        f"  Trades:       {m['total_trades']} (W:{m['wins']} L:{m['losses']})\n"
        f"  Win Rate:     {m['win_rate_pct']}%\n"
        f"  Expectância:  {m['expectancy_r']:+.3f}R\n"
        f"  Sharpe:       {m['sharpe_ratio']}\n"
        f"  Max DD:       {m['max_drawdown_pct']:.1f}%\n"
        f"  Retorno Total:{m['total_return_pct']:+.1f}%\n"
        f"  Capital Final: ${m['final_capital']:,.2f}\n"
        f"{'='*55}"
    )

    return doc


def main():
    parser = argparse.ArgumentParser(description="Trinity Trading Backtest Engine")
    parser.add_argument("--symbol",    default="BTC/USDT:USDT", help="Par a backtesting")
    parser.add_argument("--days",      type=int,   default=180,  help="Período em dias")
    parser.add_argument("--threshold", type=float, default=55.0, help="Score SMC mínimo")
    parser.add_argument("--capital",   type=float, default=10000.0, help="Capital inicial")
    args = parser.parse_args()

    run(symbol=args.symbol, days=args.days, threshold=args.threshold,
        initial_capital=args.capital)


if __name__ == "__main__":
    main()
