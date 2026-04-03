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
_MEXC_TICKER  = "https://api.mexc.com/api/v3/ticker/24hr"
_MEXC_KLINES  = "https://api.mexc.com/api/v3/klines"
_INTERVAL_MAP = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m"}
_price_cache: dict = {"price": None, "cached_at": 0.0}

# ── Liquidation heatmap cache (Coinglass v3) ──────────────────────────────────
_CG_LIQ_MAP   = "https://open-api-v3.coinglass.com/api/futures/liquidation/map"
_liq_cache: dict = {"data": None, "cached_at": 0.0}

# ── Apify screenshot cache (heatmap imagem) ───────────────────────────────────
_APIFY_ACTOR  = "ped2QOnVXksRv4Fx0"
_liq_img: dict = {"url": None, "cached_at": 0.0, "error": None}


async def _run_apify_heatmap():
    """Captura screenshot do heatmap Coinglass via Apify Actor."""
    import os as _os
    token = _os.getenv("APIFY_TOKEN", "")
    if not token:
        _liq_img["error"] = "APIFY_TOKEN não configurado"
        return
    try:
        # 1. Iniciar run
        def _start():
            return _req.post(
                f"https://api.apify.com/v2/acts/{_APIFY_ACTOR}/runs",
                headers={"Authorization": f"Bearer {token}"},
                json={"coin": "BTC", "type": "symbol", "width": 1280,
                      "height": 720, "waitTime": 10, "headless": True},
                timeout=15,
            ).json()

        run_data  = await asyncio.to_thread(_start)
        run_id    = run_data.get("data", {}).get("id")
        ds_id     = run_data.get("data", {}).get("defaultDatasetId")
        if not run_id:
            _liq_img["error"] = f"actor não iniciou: {str(run_data)[:200]}"
            return

        # 2. Aguardar conclusão (poll 8s, máx 3min)
        for _ in range(23):
            await asyncio.sleep(8)
            def _check(rid=run_id):
                return _req.get(
                    f"https://api.apify.com/v2/actor-runs/{rid}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                ).json()
            status = (await asyncio.to_thread(_check)).get("data", {}).get("status", "")
            if status == "SUCCEEDED":
                break
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                _liq_img["error"] = f"run {status}"
                return

        # 3. Pegar URL da imagem no dataset
        def _items(did=ds_id):
            return _req.get(
                f"https://api.apify.com/v2/datasets/{did}/items",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            ).json()

        items = await asyncio.to_thread(_items)
        if isinstance(items, list) and items:
            item = items[0]
            url  = (item.get("imageUrl") or item.get("url") or
                    item.get("screenshotUrl") or item.get("imageStorageUrl"))
            if url:
                _liq_img.update({"url": url, "cached_at": _time.time(), "error": None})
                return
        _liq_img["error"] = f"sem URL no dataset: {str(items)[:200]}"

    except Exception as e:
        _liq_img["error"] = str(e)


async def _apify_loop():
    """Roda o actor a cada 100 minutos em background."""
    while True:
        await _run_apify_heatmap()
        await asyncio.sleep(6000)   # 100 minutos


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(_apify_loop())
    # Inicia engine de liquidações Binance como task async
    try:
        from btc_liquidation_engine import start_async as _liq_async
        asyncio.create_task(_liq_async())
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).warning(f"Liquidation engine não iniciado: {_e}")


@app.get("/api/liq-screenshot")
async def get_liq_screenshot():
    """URL da última screenshot do heatmap (Apify)."""
    return JSONResponse(content=_liq_img)


@app.get("/api/liquidations-live")
async def get_liquidations_live():
    """Liquidações BTC em tempo real — WebSocket Binance (janelas 1m/5m/15m)."""
    try:
        from btc_liquidation_engine import get_snapshot as _liq_snap
        return JSONResponse(content=_liq_snap())
    except Exception as e:
        return JSONResponse(content={"error": str(e), "connected": False})


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
        def _fetch():
            r = _req.get(_MEXC_TICKER, params={"symbol": "BTCUSDT"}, timeout=3)
            r.raise_for_status()
            return r.json()
        d = await asyncio.to_thread(_fetch)
        _price_cache.update({
            "price":      float(d["lastPrice"]),
            "change_24h": float(d["priceChangePercent"]),
            "high_24h":   float(d["highPrice"]),
            "low_24h":    float(d["lowPrice"]),
            "timestamp":  d.get("closeTime"),
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


@app.get("/api/candles")
async def get_candles(interval: str = "1m", limit: int = 60):
    """OHLCV candles para sparkline — MEXC public API."""
    tf = _INTERVAL_MAP.get(interval, "1m")
    try:
        def _fetch():
            r = _req.get(_MEXC_KLINES, params={"symbol": "BTCUSDT", "interval": tf, "limit": min(limit, 200)}, timeout=5)
            r.raise_for_status()
            return r.json()
        raw = await asyncio.to_thread(_fetch)
        candles = [{"t": c[0], "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4]), "v": float(c[5])} for c in raw]
        return JSONResponse(content=candles)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/liquidations")
async def get_liquidations():
    """Heatmap de liquidações BTC via Coinglass v3 — cache 5min."""
    import os as _os
    _CACHE_TTL = 300.0   # 5 minutos — evita rate limit 429
    now    = _time.time()
    cached = _liq_cache["data"]
    if cached and (now - _liq_cache["cached_at"]) < _CACHE_TTL:
        return JSONResponse(content=cached)
    try:
        cg_key = _os.getenv("COINGLASS_API_KEY", "")

        def _fetch():
            r = _req.get(
                _CG_LIQ_MAP,
                headers={"CG-API-KEY": cg_key},
                params={"symbol": "BTC", "timeframe": "12h"},
                timeout=8,
            )
            return r.status_code, r.json()

        status, raw = await asyncio.to_thread(_fetch)

        # Rate limit ou erro de API — mantém cache anterior se existir
        if status == 429 or not raw.get("success", True) is not False and str(raw.get("code","")) == "429":
            if cached:
                return JSONResponse(content=cached)
            return JSONResponse(content={"levels": [], "error": f"rate limit ({status})"})

        d = raw.get("data") or {}

        # Suporta diferentes formatos de resposta Coinglass v3
        prices = d.get("y")      or d.get("priceList")    or []
        longs  = d.get("longs")  or d.get("longLiqList")  or d.get("longList")  or []
        shorts = d.get("shorts") or d.get("shortLiqList") or d.get("shortList") or []

        levels = []
        for i, p in enumerate(prices):
            lv = float(longs[i])  if i < len(longs)  else 0.0
            sv = float(shorts[i]) if i < len(shorts) else 0.0
            if lv + sv > 0:
                levels.append({
                    "price":     round(float(p), 2),
                    "long_usd":  round(lv  / 1_000_000, 3),  # → $M
                    "short_usd": round(sv / 1_000_000, 3),
                })

        # Se retornou 0 níveis, mantém cache anterior e retorna debug
        if not levels:
            if cached:
                return JSONResponse(content=cached)
            result = {"levels": [], "error": "API retornou 0 níveis", "api_debug": str(raw)[:500]}
            return JSONResponse(content=result)

        result = {"levels": levels, "ts": int(now)}
        _liq_cache.update({"data": result, "cached_at": now})
        return JSONResponse(content=result)

    except Exception as e:
        # Em caso de exceção, mantém cache anterior se existir
        if cached:
            return JSONResponse(content=cached)
        return JSONResponse(content={"levels": [], "error": str(e)})


# Serve frontend
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
