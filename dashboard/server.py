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

BASE_DIR         = Path(__file__).parent.parent
STATE_FILE       = BASE_DIR / "dashboard" / "current_state.json"
CRASH_SCAN_FILE  = BASE_DIR / "dashboard" / "crash_scan_latest.json"
PUMP_SCAN_FILE   = BASE_DIR / "dashboard" / "pump_scan_latest.json"
BACKTEST_FILE    = BASE_DIR / "dashboard" / "backtest_results.json"
ALTCOIN_SCAN_FILE = BASE_DIR / "dashboard" / "altcoin_scan_latest.json"
LOGS_DIR         = BASE_DIR / "logs"
STATIC_DIR       = BASE_DIR / "dashboard" / "static"

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


_analysis_running = False   # flag para evitar runs concorrentes


async def _crash_scan_loop():
    """Roda o Predictive Crash Trader a cada 30s em background (scan + alertas Telegram)."""
    import logging as _log
    _clog = _log.getLogger("crash_trader")
    # Aguarda 20s no startup para não competir com a análise institucional
    await asyncio.sleep(20)
    while True:
        try:
            import sys as _sys
            _sys.path.insert(0, str(BASE_DIR))
            from trinity.traders.predictive_crash_trader.predictive_crash_trader import run_crash_cycle
            result = await asyncio.to_thread(run_crash_cycle)
            _clog.debug(
                f"Crash scan: {result.get('coins_scanned', 0)} coins | "
                f"{len(result.get('candidates', []))} candidatos | "
                f"{result.get('scan_duration_s', 0):.1f}s"
            )
        except Exception as _e:
            _clog.error(f"Crash scan loop error: {_e}")
        await asyncio.sleep(30)


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
    # Roda análise imediatamente ao subir (Render free tier: ephemerals FS fix)
    asyncio.create_task(_run_analysis_bg())
    # Crash Radar — loop background
    asyncio.create_task(_crash_scan_loop())
    # Pump Radar — loop background (offset 10s para não competir com crash scan)
    asyncio.create_task(_pump_scan_loop())
    # Altcoin Scanner SMC — loop background a cada 5 min
    asyncio.create_task(_altcoin_scan_loop())


async def _run_analysis_bg(force: bool = False):
    """
    Roda run_institutional_analysis em thread separada (não bloqueia event loop).
    Só executa se dados estiverem velhos (> 45 min) ou force=True.
    Evita runs concorrentes via _analysis_running flag.
    """
    global _analysis_running
    if _analysis_running:
        return

    # Verifica se o estado já é recente (scheduler do start.py pode ter rodado primeiro)
    if not force and STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            last  = state.get("last_updated")
            if last:
                age_min = (_time.time() - _time.mktime(_time.strptime(last[:19], "%Y-%m-%dT%H:%M:%S"))) / 60
                if age_min < 45:   # menos de 45 min → não precisa rodar
                    return
        except Exception:
            pass  # em caso de erro, roda mesmo assim

    _analysis_running = True
    try:
        import sys as _sys
        _sys.path.insert(0, str(BASE_DIR))
        from main import run_institutional_analysis as _run
        await asyncio.to_thread(_run)
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).warning(f"Background analysis falhou: {_e}")
    finally:
        _analysis_running = False


@app.get("/api/run-now")
async def trigger_analysis():
    """
    Força análise institucional imediata — para cron externo (cron-job.org / UptimeRobot).
    Retorna 202 se iniciou, 409 se já está rodando.
    """
    global _analysis_running
    if _analysis_running:
        return JSONResponse(content={"status": "already_running"}, status_code=409)
    asyncio.create_task(_run_analysis_bg(force=True))
    return JSONResponse(content={"status": "started"}, status_code=202)


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


@app.get("/api/backtest-results")
def get_backtest_results():
    """Resultados do backtest walk-forward — métricas, equity curve e trades."""
    if BACKTEST_FILE.exists():
        return JSONResponse(content=json.loads(BACKTEST_FILE.read_text()))
    return JSONResponse(content={"status": "no_data", "trades": [], "metrics": {}, "equity_curve": []})


_backtest_running = False


async def _run_backtest_task():
    """Executa backtest em background e salva resultados."""
    global _backtest_running
    _backtest_running = True
    try:
        import sys as _sys
        _sys.path.insert(0, str(BASE_DIR))
        from backtester.run_backtest import run as _bt_run
        await asyncio.to_thread(_bt_run)
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).error(f"Backtest task falhou: {_e}")
    finally:
        _backtest_running = False


@app.get("/api/run-backtest")
async def trigger_backtest():
    """Dispara backtest assíncrono — retorna 202 se iniciou, 409 se já em execução."""
    global _backtest_running
    if _backtest_running:
        return JSONResponse(content={"status": "already_running"}, status_code=409)
    asyncio.create_task(_run_backtest_task())
    return JSONResponse(content={"status": "started"}, status_code=202)


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


async def _pump_scan_loop():
    """Roda o Predictive Pump Trader a cada 30s em background (scan + alertas Telegram)."""
    import logging as _log
    _plog = _log.getLogger("pump_trader")
    await asyncio.sleep(30)  # offset: crash começa em 20s, pump em 30s
    while True:
        try:
            import sys as _sys
            _sys.path.insert(0, str(BASE_DIR))
            from trinity.traders.predictive_pump_trader.predictive_pump_trader import run_pump_cycle
            result = await asyncio.to_thread(run_pump_cycle)
            _plog.debug(
                f"Pump scan: {result.get('coins_scanned', 0)} coins | "
                f"{len(result.get('candidates', []))} candidatos | "
                f"{result.get('scan_duration_s', 0):.1f}s"
            )
        except Exception as _e:
            _plog.error(f"Pump scan loop error: {_e}")
        await asyncio.sleep(30)


@app.get("/api/pump-scanner")
def get_pump_scanner():
    """Último resultado do Predictive Pump Trader (Cap. 20)."""
    if PUMP_SCAN_FILE.exists():
        try:
            return JSONResponse(content=json.loads(PUMP_SCAN_FILE.read_text()))
        except Exception:
            pass
    return JSONResponse(content={"scan_ts": None, "candidates": [], "coins_scanned": 0})


@app.get("/api/crash-scanner")
def get_crash_scanner():
    """Último resultado do Predictive Crash Trader (Cap. 19)."""
    if CRASH_SCAN_FILE.exists():
        try:
            return JSONResponse(content=json.loads(CRASH_SCAN_FILE.read_text()))
        except Exception:
            pass
    return JSONResponse(content={"scan_ts": None, "candidates": [], "coins_scanned": 0})


async def _altcoin_scan_loop():
    """Roda o altcoin scanner a cada 5 min em background."""
    import logging as _log
    _alog = _log.getLogger("altcoin_scanner")
    await asyncio.sleep(45)  # offset: começa 45s após startup
    while True:
        try:
            import sys as _sys
            _sys.path.insert(0, str(BASE_DIR))
            from trinity.traders.altcoin_scanner.altcoin_scanner import run_altcoin_scan
            result = await asyncio.to_thread(run_altcoin_scan)
            try:
                ALTCOIN_SCAN_FILE.write_text(
                    __import__("json").dumps(result, indent=2, default=str)
                )
            except Exception as _we:
                _alog.warning(f"Altcoin scan write error: {_we}")
        except Exception as _e:
            _alog.error(f"Altcoin scan loop error: {_e}")
        await asyncio.sleep(300)  # 5 minutos


@app.get("/api/altcoin-scanner")
def get_altcoin_scanner():
    """Último resultado do Altcoin Scanner SMC."""
    if ALTCOIN_SCAN_FILE.exists():
        try:
            return JSONResponse(content=json.loads(ALTCOIN_SCAN_FILE.read_text()))
        except Exception:
            pass
    return JSONResponse(content={"scan_ts": None, "candidates": [], "coins_scanned": 0})


# ── Aliases para o React frontend v3.0 ───────────────────────────────────────

@app.get("/api/current-state")
def get_current_state():
    """Alias de /api/status para o React frontend."""
    if STATE_FILE.exists():
        return JSONResponse(content=json.loads(STATE_FILE.read_text()))
    return JSONResponse(content={"status": "no_data"})


@app.get("/api/signal-history")
def get_signal_history(limit: int = 20):
    """Alias de /api/signals para o React frontend."""
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


@app.get("/api/crash-scan")
def get_crash_scan():
    """Alias de /api/crash-scanner para o React frontend."""
    if CRASH_SCAN_FILE.exists():
        try:
            return JSONResponse(content=json.loads(CRASH_SCAN_FILE.read_text()))
        except Exception:
            pass
    return JSONResponse(content={"scan_ts": None, "candidates": [], "coins_scanned": 0})


@app.get("/api/pump-scan")
def get_pump_scan():
    """Alias de /api/pump-scanner para o React frontend."""
    if PUMP_SCAN_FILE.exists():
        try:
            return JSONResponse(content=json.loads(PUMP_SCAN_FILE.read_text()))
        except Exception:
            pass
    return JSONResponse(content={"scan_ts": None, "candidates": [], "coins_scanned": 0})


# ── Serve React app v3.0 em /app/ ────────────────────────────────────────────
REACT_APP_DIR = BASE_DIR / "dashboard" / "static" / "app"
if REACT_APP_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(REACT_APP_DIR), html=True), name="react-app")

# Serve frontend (legacy HTML) — deve vir POR ÚLTIMO
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
