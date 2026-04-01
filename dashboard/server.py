"""
dashboard/server.py — Servidor do Dashboard de Trading
Execute com: uvicorn dashboard.server:app --host 0.0.0.0 --port 8000 --reload
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pathlib import Path
import json
import glob
import asyncio
import time as _time
import requests as _req

BASE_DIR   = Path(__file__).parent.parent
STATE_FILE = BASE_DIR / "dashboard" / "current_state.json"
LOGS_DIR   = BASE_DIR / "logs"
STATIC_DIR = BASE_DIR / "dashboard" / "static"

app = FastAPI(title="QuantDesk", version="1.0")

# ── Price ticker cache ────────────────────────────────────────────────────────
_exchange    = ccxt.mexc({"enableRateLimit": False})
_price_cache: dict = {"price": None, "cached_at": 0.0}


@app.get("/api/status")
def get_status():
    """Estado atual do mercado — última análise institucional."""
    if STATE_FILE.exists():
        return JSONResponse(content=json.loads(STATE_FILE.read_text()))
    return JSONResponse(content={"status": "no_data"})


@app.get("/api/price")
async def get_price():
    """Preço BTC/USDT em tempo real — cache 1 segundo."""
    now = _time.time()
    if _price_cache["price"] and (now - _price_cache["cached_at"]) < 1.0:
        return JSONResponse(content={k: v for k, v in _price_cache.items() if k != "cached_at"})
    try:
        ticker = await asyncio.to_thread(_exchange.fetch_ticker, "BTC/USDT")
        _price_cache.update({
            "price":      ticker["last"],
            "change_24h": ticker.get("percentage"),
            "high_24h":   ticker.get("high"),
            "low_24h":    ticker.get("low"),
            "timestamp":  ticker.get("datetime"),
            "cached_at":  now,
        })
        _price_cache.pop("error", None)
    except Exception as e:
        _price_cache.update({"error": str(e), "cached_at": now})
    return JSONResponse(content={k: v for k, v in _price_cache.items() if k != "cached_at"})


@app.get("/api/signals")
def get_signals(limit: int = 30):
    """Histórico de sinais institucionais dos últimos 7 dias."""
    signals = []
    files   = sorted(glob.glob(str(LOGS_DIR / "institutional_*.jsonl")), reverse=True)[:7]
    for filepath in files:
        try:
            with open(filepath) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        signals.append(json.loads(line))
        except Exception:
            pass
    signals.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return JSONResponse(content=signals[:limit])


# Serve frontend
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
