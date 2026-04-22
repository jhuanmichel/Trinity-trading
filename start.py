"""
start.py — Entrypoint para produção (Render/VPS)
Roda o bot scheduler em background + dashboard FastAPI na porta $PORT
"""
import os
import threading
import time
import logging

log = logging.getLogger(__name__)


def run_seasonality_recalc():
    """Recalcula dados sazonais (roda mensalmente). Atualiza singleton em memória."""
    try:
        import subprocess
        log.info("[SEASONALITY] Iniciando recalculo mensal de dados sazonais...")
        result = subprocess.run(
            ["python", "scripts/calculate_seasonality.py"],
            timeout=300,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            from seasonality_engine import get_seasonality_engine
            get_seasonality_engine().reload()
            log.info("[SEASONALITY] Dados sazonais atualizados com sucesso")
        else:
            log.error(f"[SEASONALITY] Script retornou erro: {result.stderr[:200]}")
    except Exception as e:
        log.error(f"[SEASONALITY] Erro no recalculo mensal: {e}")


def run_monthly_backtest():
    """Executa backtest walk-forward mensal. Usa parquet histórico se disponível."""
    try:
        log.info("[BACKTEST] Iniciando backtest mensal...")
        from backtester.run_backtest import run as _bt_run
        result = _bt_run()
        trades = result.get("metrics", {}).get("total_trades", 0)
        log.info(f"[BACKTEST] Backtest mensal concluído — {trades} trades")
    except Exception as e:
        log.error(f"[BACKTEST] Erro no backtest mensal: {e}")


def _run_scheduler():
    import schedule
    from main import run_analysis_summary, run_analysis_signal, run_institutional_analysis, run_pump_radar, run_crash_radar
    from config import SUMMARY_INTERVAL_MINUTES, SIGNAL_INTERVAL_MINUTES, INST_INTERVAL_MINUTES
    from weight_optimizer import run_optimization_report

    schedule.every(SUMMARY_INTERVAL_MINUTES).minutes.do(run_analysis_summary)
    schedule.every(SIGNAL_INTERVAL_MINUTES).minutes.do(run_analysis_signal)
    schedule.every(INST_INTERVAL_MINUTES).minutes.do(run_institutional_analysis)
    schedule.every(30).minutes.do(run_pump_radar)
    schedule.every(30).minutes.do(run_crash_radar)
    # Relatório de otimização de pesos — gerado uma vez por dia às 00:05 UTC
    schedule.every().day.at("00:05").do(run_optimization_report)
    # Recalculo mensal de sazonalidade (dados históricos Binance)
    schedule.every(30).days.do(run_seasonality_recalc)
    # Backtest walk-forward mensal (atualiza métricas e equity curve)
    schedule.every(30).days.do(run_monthly_backtest)

    # Weekly digest de performance — todo domingo às 20:00 UTC
    def run_weekly_digest():
        try:
            from trinity.notifications.weekly_digest import WeeklyDigest
            WeeklyDigest().send()
        except Exception as _e:
            log.error(f"[WeeklyDigest] Erro: {_e}")

    schedule.every().sunday.at("20:00").do(run_weekly_digest)

    # Full Market Scanner — varre todos os contratos MEXC a cada 90s
    from full_market_scanner import FullMarketScanner as _FMSClass
    _fms_instance = _FMSClass()

    def _run_fms_cycle():
        try:
            _fms_instance.run_full_scan()
        except Exception as e:
            log.error(f"[FMS] Erro no ciclo: {e}")

    schedule.every(90).seconds.do(_run_fms_cycle)

    # Parabolic Scanner — detecta movimentos parabólicos a cada 15 min (RAVE-type)
    from trinity.modules.parabolic_scanner import run_parabolic_scan
    schedule.every(15).minutes.do(run_parabolic_scan)

    # Market Movers Scanner — escaneia todos os contratos MEXC a cada 10 min
    from trinity.modules.market_movers import get_scanner as _get_movers_scanner
    _movers_scanner = _get_movers_scanner()
    schedule.every(10).minutes.do(_movers_scanner.scan)

    # Outcome Health Monitor — verifica saúde do registro a cada 1h
    from trinity.modules.outcome_health_monitor import get_monitor as _get_outcome_monitor
    _outcome_health = _get_outcome_monitor()   # auto-detecta /data/logs ou logs/
    schedule.every(1).hours.do(_outcome_health.check)

    # Reversal Hunter — diagnostico horario (Go/No-Go gate) acumula por 24h antes de decidir
    # Implementacao do Reversal Hunter em si fica aguardando analyze_pump_diag.py
    def run_pump_diag():
        try:
            from scripts.diag_pump_candidates_hourly import run_pump_diag as _run
            _run()
        except Exception as _e:
            log.error(f"[DiagHourly] Erro: {_e}")

    schedule.every(1).hours.do(run_pump_diag)

    log.info("Bot scheduler iniciado.")
    run_institutional_analysis()   # roda imediatamente ao subir
    run_optimization_report()      # gera relatório inicial (pode ser "insufficient_data")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    import uvicorn
    from btc_liquidation_engine import start_background as _start_liq

    # Inicia engine de liquidações Binance em background thread
    _start_liq()

    t = threading.Thread(target=_run_scheduler, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=port)
