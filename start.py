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
    from main import run_analysis_summary, run_analysis_signal, run_institutional_analysis
    from config import SUMMARY_INTERVAL_MINUTES, SIGNAL_INTERVAL_MINUTES, INST_INTERVAL_MINUTES

    schedule.every(SUMMARY_INTERVAL_MINUTES).minutes.do(run_analysis_summary)
    schedule.every(SIGNAL_INTERVAL_MINUTES).minutes.do(run_analysis_signal)
    schedule.every(INST_INTERVAL_MINUTES).minutes.do(run_institutional_analysis)

    log.info("Bot scheduler iniciado.")
    run_institutional_analysis()   # roda imediatamente ao subir

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    import uvicorn

    t = threading.Thread(target=_run_scheduler, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=port)
