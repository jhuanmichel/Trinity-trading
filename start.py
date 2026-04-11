"""
start.py — Entrypoint para produção (Render/VPS)
Roda o bot scheduler em background + dashboard FastAPI na porta $PORT
"""
import os
import threading
import time
import logging

log = logging.getLogger(__name__)


def _run_scheduler():
    import schedule
    from main import run_analysis_summary, run_analysis_signal, run_institutional_analysis, run_pump_radar
    from config import SUMMARY_INTERVAL_MINUTES, SIGNAL_INTERVAL_MINUTES, INST_INTERVAL_MINUTES
    from weight_optimizer import run_optimization_report

    schedule.every(SUMMARY_INTERVAL_MINUTES).minutes.do(run_analysis_summary)
    schedule.every(SIGNAL_INTERVAL_MINUTES).minutes.do(run_analysis_signal)
    schedule.every(INST_INTERVAL_MINUTES).minutes.do(run_institutional_analysis)
    schedule.every(30).minutes.do(run_pump_radar)
    # Relatório de otimização de pesos — gerado uma vez por dia às 00:05 UTC
    schedule.every().day.at("00:05").do(run_optimization_report)

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
