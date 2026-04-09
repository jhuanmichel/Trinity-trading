# pip install -r requirements.txt
"""
api.py — Backend Flask do Altcoin Radar
Rodar com: python api.py
"""

import json
import os
import threading
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, abort
from flask_cors import CORS

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
EXCHANGE        = os.getenv("EXCHANGE", "mexc").lower()
MEXC_API_KEY    = os.getenv("MEXC_API_KEY", "")
MEXC_API_SECRET = os.getenv("MEXC_API_SECRET", "")
GATE_API_KEY    = os.getenv("GATE_API_KEY", "")
GATE_API_SECRET = os.getenv("GATE_API_SECRET", "")
SCAN_INTERVAL   = int(os.getenv("SCAN_INTERVAL", "300"))
PORT            = int(os.getenv("PORT", "5000"))

BASE_DIR    = Path(__file__).parent
SIGNALS_FILE = BASE_DIR / "data" / "signals.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ── Cache em memória ──────────────────────────────────────────────────────────
_cache: dict = {"signals": [], "updated_at": None}
_cache_lock  = threading.Lock()

# ── Funding score ─────────────────────────────────────────────────────────────
def calc_funding_score(funding_pct: float, direction: str) -> int:
    """Retorna -2 a +2 baseado no funding rate (em %) e direção do trade."""
    fr = funding_pct
    if direction == "LONG":
        if fr < -0.03:   return  2
        if fr < -0.01:   return  1
        if fr <= 0.03:   return  0
        if fr <= 0.07:   return -1
        return -2
    else:  # SHORT
        if fr > 0.03:    return  2
        if fr > 0.01:    return  1
        if fr >= -0.01:  return  0
        if fr >= -0.03:  return -1
        return -2

def funding_label(score: int) -> str:
    labels = {2: "Confirma forte", 1: "Favorável", 0: "Neutro", -1: "Contra sinal", -2: "Bloquear"}
    return labels.get(score, "Neutro")

# ── Adaptadores de funding por exchange ───────────────────────────────────────
def _fetch_funding_mexc(symbol: str) -> Optional[dict]:
    """Funding via MEXC Contract REST (sem auth)."""
    try:
        sym = symbol.upper() + "_USDT"
        url = f"https://contract.mexc.com/api/v1/contract/funding_rate/{sym}"
        r   = requests.get(url, timeout=8)
        d   = r.json().get("data", {})
        fr  = float(d.get("fundingRate", 0)) * 100
        nts = int(d.get("nextSettleTime", 0))

        # Histórico
        hist_url = f"https://contract.mexc.com/api/v1/contract/funding_rate/history"
        hr = requests.get(hist_url, params={"symbol": sym, "page_num": 1, "page_size": 8}, timeout=8)
        raw = hr.json().get("data", {}).get("resultList", [])
        history = [round(float(x["fundingRate"]) * 100, 5) for x in reversed(raw)]

        return {"funding": round(fr, 5), "next_ts": nts, "history": history}
    except Exception as e:
        log.warning(f"[mexc funding] {symbol}: {e}")
        return None

def _fetch_funding_gateio(symbol: str) -> Optional[dict]:
    """Funding via Gate.io v4 REST (sem auth)."""
    try:
        sym = symbol.upper() + "_USDT"
        url = f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{sym}"
        r   = requests.get(url, timeout=8)
        c   = r.json()
        fr  = float(c.get("funding_rate", 0)) * 100
        nts = int(float(c.get("funding_next_apply", 0)) * 1000)

        hist_url = f"https://api.gateio.ws/api/v4/futures/usdt/funding_rate"
        hr = requests.get(hist_url, params={"contract": sym, "limit": 8}, timeout=8)
        history = [round(float(x["r"]) * 100, 5) for x in reversed(hr.json())]

        return {"funding": round(fr, 5), "next_ts": nts, "history": history}
    except Exception as e:
        log.warning(f"[gateio funding] {symbol}: {e}")
        return None

def fetch_funding(symbol: str) -> dict:
    """Busca funding com fallback mock."""
    fetcher = _fetch_funding_mexc if EXCHANGE == "mexc" else _fetch_funding_gateio
    data = fetcher(symbol)
    if data:
        return data
    # Fallback: retorna mock do signals.json
    with _cache_lock:
        sig = next((s for s in _cache["signals"] if s["symbol"] == symbol.upper()), None)
    fr = sig["funding"] if sig else 0.0
    return {"funding": fr, "next_ts": 0, "history": [fr] * 8}

# ── Leitura/escrita do cache ──────────────────────────────────────────────────
def _load_signals_file() -> list:
    if SIGNALS_FILE.exists():
        try:
            return json.loads(SIGNALS_FILE.read_text())["signals"]
        except Exception:
            pass
    return []

def _save_signals_file(signals: list):
    SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "signals": signals}
    SIGNALS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

def _enrich(sig: dict) -> dict:
    """Adiciona campos derivados ao signal."""
    direction = sig.get("direction", "NEUTRO")
    fr        = sig.get("funding", 0.0)
    fs        = calc_funding_score(fr, direction)
    return {
        **sig,
        "fundingScore":  fs,
        "fundingLabel":  funding_label(fs),
        "nextScan":      sig.get("nextScan", SCAN_INTERVAL),
    }

def refresh_cache():
    """Recarrega do arquivo e enriquece. Chamado pelo background thread."""
    raw = _load_signals_file()
    enriched = [_enrich(s) for s in raw]
    with _cache_lock:
        _cache["signals"]    = enriched
        _cache["updated_at"] = datetime.now(timezone.utc).isoformat()
    log.info(f"[cache] {len(enriched)} signals carregados")

def _background_loop():
    while True:
        try:
            refresh_cache()
        except Exception as e:
            log.error(f"[bg loop] {e}")
        time.sleep(SCAN_INTERVAL)

# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/api/signals")
def get_signals():
    with _cache_lock:
        signals = list(_cache["signals"])
        updated = _cache["updated_at"]
    return jsonify({"updated_at": updated, "signals": signals})

@app.get("/api/signal/<symbol>")
def get_signal(symbol: str):
    sym = symbol.upper()
    with _cache_lock:
        sig = next((s for s in _cache["signals"] if s["symbol"] == sym), None)
    if not sig:
        abort(404, description=f"Symbol {sym} not found")
    return jsonify(sig)

@app.get("/api/funding/<symbol>")
def get_funding(symbol: str):
    data = fetch_funding(symbol.upper())
    sig  = None
    with _cache_lock:
        sig = next((s for s in _cache["signals"] if s["symbol"] == symbol.upper()), None)
    direction = sig["direction"] if sig else "LONG"
    fs = calc_funding_score(data["funding"], direction)
    return jsonify({
        "symbol":       symbol.upper(),
        "funding":      data["funding"],
        "next_ts":      data["next_ts"],
        "history":      data["history"],
        "score":        fs,
        "label":        funding_label(fs),
    })

@app.get("/api/health")
def health():
    with _cache_lock:
        n = len(_cache["signals"])
        u = _cache["updated_at"]
    return jsonify({"status": "ok", "signals": n, "updated_at": u})

# ── Init ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    refresh_cache()   # carrega imediatamente
    t = threading.Thread(target=_background_loop, daemon=True)
    t.start()
    log.info(f"Altcoin Radar API rodando na porta {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
