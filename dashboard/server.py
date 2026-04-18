"""
dashboard/server.py — Servidor do Dashboard de Trading
Execute com: uvicorn dashboard.server:app --host 0.0.0.0 --port 8000 --reload
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, Response
from pathlib import Path
import json
import glob
import asyncio
import time as _time
import requests as _req
from trinity.traders.daily_summary import should_send_summary, send_daily_summary

BASE_DIR          = Path(__file__).parent.parent
STATE_FILE        = BASE_DIR / "dashboard" / "current_state.json"
CRASH_SCAN_FILE   = BASE_DIR / "dashboard" / "crash_scan_latest.json"
PUMP_SCAN_FILE    = BASE_DIR / "dashboard" / "pump_scan_latest.json"
BACKTEST_FILE     = BASE_DIR / "dashboard" / "backtest_results.json"
ALTCOIN_SCAN_FILE = BASE_DIR / "dashboard" / "altcoin_scan_latest.json"
WIN_RATE_FILE     = BASE_DIR / "dashboard" / "win_rate.json"
OPT_REPORT_FILE   = BASE_DIR / "dashboard" / "optimization_report.json"
FMS_SCAN_FILE     = BASE_DIR / "dashboard" / "full_market_scan.json"
FUNDING_SCAN_FILE = BASE_DIR / "dashboard" / "funding_extreme_latest.json"
LOGS_DIR          = BASE_DIR / "logs"
STATIC_DIR        = BASE_DIR / "dashboard" / "static"

app = FastAPI(title="QuantDesk", version="1.0")


@app.get("/health")
def health():
    """Health check — responde imediatamente, sem I/O nem dependências externas."""
    return {"status": "ok"}


# ── Full Market Scanner singleton ─────────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(BASE_DIR))
from full_market_scanner import FullMarketScanner as _FMSClass
_fms = _FMSClass()

# ── Exchange Manager — inicializado em background 10s após startup ─────────────
# Não importar nem instanciar no nível de módulo — bloqueia o boot síncrono
_ex_mgr = None  # setado por _delayed_warmup()

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


async def _delayed_warmup():
    """
    Inicializa o ExchangeManager e aquece o cache 10s após o servidor subir.
    Separado do módulo-level para não bloquear o boot síncrono do uvicorn.
    """
    import logging as _wlog_mod
    _wlog = _wlog_mod.getLogger("startup.warmup")
    await asyncio.sleep(2)    # deixa o servidor aceitar requests primeiro
    global _ex_mgr
    try:
        from trinity.exchanges.exchange_manager import get_exchange_manager as _get_ex_mgr
        _ex_mgr = _get_ex_mgr()
        _wlog.info("[WARMUP] ExchangeManager criado")
        await asyncio.to_thread(_ex_mgr.fetch_all_tickers_unified)
        _wlog.info("[WARMUP] ExchangeManager warmup concluído (cache quente)")
    except Exception as _e:
        _wlog.error(f"[WARMUP] ExchangeManager erro: {_e}")


async def _keep_alive():
    """Self-ping a cada 4 minutos para evitar cold start do Render free tier."""
    await asyncio.sleep(30)   # aguarda boot completo
    while True:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _req.get(
                    "https://trinity-trading.onrender.com/health",
                    timeout=10,
                )
            )
        except Exception:
            pass
        await asyncio.sleep(240)   # 4 minutos


@app.on_event("startup")
async def startup_event():
    # ExchangeManager — init e warmup tardio (10s) para não bloquear o boot
    asyncio.create_task(_delayed_warmup())
    asyncio.create_task(_keep_alive())
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
    # Outcome Tracker — verifica pendentes a cada 15 min
    asyncio.create_task(_outcome_check_loop())
    # News Sentinel — monitoramento de notícias macro a cada 2 min
    asyncio.create_task(_news_sentinel_loop())
    # Full Market Scanner — varre todos os contratos MEXC a cada 90s
    asyncio.create_task(_fms_loop())
    # Funding Extreme Scanner — detecta funding extremo em todos contratos a cada 120s
    asyncio.create_task(_funding_scan_loop())
    # Daily Summary — resumo diário às 08:00 UTC via Telegram
    asyncio.create_task(_daily_summary_loop())


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
        try:
            return JSONResponse(content=json.loads(STATE_FILE.read_text()))
        except Exception:
            pass
    return JSONResponse(content={"status": "no_data"})


@app.get("/api/backtest-results")
def get_backtest_results():
    """Resultados do backtest walk-forward — métricas, equity curve e trades."""
    if BACKTEST_FILE.exists():
        try:
            return JSONResponse(content=json.loads(BACKTEST_FILE.read_text()))
        except Exception:
            pass
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
    """Roda o Predictive Pump Trader a cada 60s em background (scan + alertas Telegram)."""
    import logging as _log
    _plog = _log.getLogger("pump_trader")
    await asyncio.sleep(45)  # offset: crash começa em 20s, pump em 45s
    while True:
        try:
            import sys as _sys
            _sys.path.insert(0, str(BASE_DIR))
            from trinity.traders.predictive_pump_trader.predictive_pump_trader import run_pump_cycle
            result = await asyncio.to_thread(run_pump_cycle)
            _plog.info(
                f"[PUMP] scan OK: {result.get('coins_scanned', 0)} coins | "
                f"{len(result.get('candidates', []))} candidatos | "
                f"{result.get('scan_duration_s', 0):.1f}s | "
                f"salvo em pump_scan_latest.json"
            )
        except Exception as _e:
            _plog.error(f"[PUMP] scan loop error: {_e}", exc_info=True)
        await asyncio.sleep(60)


async def _funding_scan_loop():
    """Roda o Funding Extreme Scanner a cada 120s em background — independente do pump/crash."""
    import logging as _log
    _flog = _log.getLogger("funding_scanner")
    await asyncio.sleep(70)  # offset: pump em 45s, funding em 70s
    while True:
        try:
            import sys as _sys
            _sys.path.insert(0, str(BASE_DIR))
            from trinity.traders.funding_extreme_scanner import run_funding_scan
            result = await asyncio.to_thread(run_funding_scan)
            _flog.info(
                f"[FUNDING] scan OK: {result.get('coins_scanned', 0)} coins | "
                f"{result.get('extremes_found', 0)} extremos | "
                f"{result.get('scan_duration_s', 0):.1f}s"
            )
        except Exception as _e:
            _flog.error(f"[FUNDING] scan loop error: {_e}", exc_info=True)
        await asyncio.sleep(120)


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


@app.get("/api/funding-extreme")
def get_funding_extreme():
    """Último resultado do Funding Extreme Scanner."""
    if FUNDING_SCAN_FILE.exists():
        try:
            return JSONResponse(content=json.loads(FUNDING_SCAN_FILE.read_text()))
        except Exception:
            pass
    return JSONResponse(content={
        "scan_ts": None, "coins_scanned": 0,
        "extremes_found": 0, "top_longs": [], "top_shorts": [], "all_extremes": []
    })


_bubbles_cache: dict = {"data": None, "cached_at": 0.0}
_BUBBLES_TTL = 60  # segundos — atualiza a cada 1 min

@app.get("/api/crypto-bubbles")
def get_crypto_bubbles():
    """
    Retorna top 80 coins por volume para o Crypto Bubbles chart.
    Usa MEXC Futures ticker (contract.mexc.com) — 1 request, todos os dados.
    Campos: symbol, price, change_pct_24h, volume_usd_24h, bubble_size, oi_usd.
    """
    now = _time.time()
    cached = _bubbles_cache.get("data")
    if cached and (now - _bubbles_cache.get("cached_at", 0)) < _BUBBLES_TTL:
        return JSONResponse(content=cached)

    try:
        r    = _req.get("https://contract.mexc.com/api/v1/contract/ticker", timeout=8)
        data = r.json().get("data", [])

        _BUBBLES_EXCLUDE = {
            'STOCK', 'ETF', 'NVDA', 'TSLA', 'AAPL', 'AMZN', 'GOOGL', 'GOOGLSTOCK',
            'META', 'MSTR', 'MSFT', 'NFLX', 'AMD', 'INTC', 'BABA', 'SNDK',
            'PYPL', 'UBER', 'ABNB', 'PLTR', 'SNAP', 'SHOP', 'HOOD', 'RBLX', 'PINS',
            'GOLD', 'SILVER', 'OIL', 'USOIL', 'UKOIL', 'COPPER', 'NATGAS',
            'SPX', 'SPY', 'NDX', 'DJI', 'VIX', 'EWY', 'EWJ', 'US30',
        }

        def _is_bubble_excluded(sym_base: str) -> bool:
            u = sym_base.upper()
            if u in _BUBBLES_EXCLUDE:
                return True
            return any(kw in u for kw in ['STOCK', 'SPX', 'NDX', 'EWJ', 'EWY', 'GOOGLSTOCK'])

        bubbles = []
        for item in data:
            try:
                sym      = item.get("symbol", "")
                if not sym.endswith("_USDT"):
                    continue
                sym_base = sym.replace("_USDT", "")
                if _is_bubble_excluded(sym_base):
                    continue
                price    = float(item.get("lastPrice", 0) or 0)
                rfr      = float(item.get("riseFallRate", 0) or 0)
                amount24 = float(item.get("amount24", 0) or 0)
                hold_vol = float(item.get("holdVol", 0) or 0)
                funding  = float(item.get("fundingRate", 0) or 0)
                if price <= 0 or amount24 < 100_000:
                    continue
                oi_usd = hold_vol * price
                bubbles.append({
                    "symbol":        sym_base,
                    "price":         price,
                    "change_pct":    round(rfr * 100, 2),
                    "volume_usd":    round(amount24, 0),
                    "oi_usd":        round(oi_usd, 0),
                    "funding_rate":  round(funding, 6),
                })
            except Exception:
                continue

        # Sort by volume desc, top 80
        bubbles.sort(key=lambda x: x["volume_usd"], reverse=True)
        bubbles = bubbles[:80]

        # Normalizar bubble_size (0-100) baseado em volume
        if bubbles:
            max_vol = bubbles[0]["volume_usd"]
            for b in bubbles:
                b["bubble_size"] = round(b["volume_usd"] / max_vol * 100, 1)

        result = {
            "fetched_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "coins": bubbles
        }
        _bubbles_cache["data"]      = result
        _bubbles_cache["cached_at"] = now
        return JSONResponse(content=result)

    except Exception as e:
        if cached:
            return JSONResponse(content=cached)
        return JSONResponse(content={"fetched_at": None, "coins": [], "error": str(e)})


@app.get("/api/full-market-scan")
async def api_full_market_scan():
    """Status completo do último ciclo do Full Market Scanner."""
    return JSONResponse(content=_fms.get_current_status())


@app.get("/api/full-market-scan/top")
async def api_full_market_scan_top():
    """Top 10 pump e crash do último ciclo (versão enxuta para o dashboard)."""
    s = _fms.get_current_status()
    return JSONResponse(content={
        "top_pump":          s.get("top_pump",  [])[:10],
        "top_crash":         s.get("top_crash", [])[:10],
        "scan_ts":           s.get("scan_ts"),
        "contracts_scanned": s.get("contracts_scanned"),
        "elapsed_seconds":   s.get("elapsed_seconds"),
        "candidates_stage2": s.get("candidates_stage2"),
    })


async def _outcome_check_loop():
    """Verifica outcomes pendentes a cada 15 minutos em background (OutcomeTracker)."""
    import logging as _log
    _olog = _log.getLogger("outcome_tracker")
    await asyncio.sleep(60)   # aguarda 1 min no startup
    while True:
        try:
            import sys as _sys
            _sys.path.insert(0, str(BASE_DIR))
            from outcome_tracker import get_tracker as _get_tracker
            resolved = await asyncio.to_thread(_get_tracker().check_pending_outcomes)
            if resolved:
                _olog.info(f"OutcomeTracker: {len(resolved)} outcome(s) resolvido(s)")
        except Exception as _e:
            _olog.error(f"Outcome check loop error: {_e}")
        await asyncio.sleep(900)   # 15 minutos


async def _news_sentinel_loop():
    """Loop de monitoramento de notícias — ciclo a cada 2 min (tier1 sempre)."""
    import logging as _log
    _nlog = _log.getLogger("news_sentinel")
    await asyncio.sleep(90)   # offset para não competir com startup
    while True:
        try:
            import sys as _sys
            _sys.path.insert(0, str(BASE_DIR))
            from news_sentinel import get_sentinel as _get_sentinel
            await asyncio.to_thread(_get_sentinel().run_cycle)
        except Exception as _e:
            _nlog.error(f"[NEWS] Erro no loop: {_e}")
        await asyncio.sleep(120)   # 2 minutos


async def _fms_loop():
    """Full Market Scanner — varre todos os contratos MEXC a cada 90s."""
    import logging as _log
    _fmslog = _log.getLogger("full_market_scanner")
    await asyncio.sleep(60)   # offset: começa 60s após startup
    while True:
        try:
            await asyncio.to_thread(_fms.run_full_scan)
        except Exception as _e:
            _fmslog.error(f"[FMS] Loop error: {_e}")
        await asyncio.sleep(90)


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


async def _daily_summary_loop():
    """Verifica a cada 5 min se deve enviar o resumo diário (às 08:00 UTC)."""
    import logging as _log
    _dslog = _log.getLogger("daily_summary")
    await asyncio.sleep(60)  # aguarda 60s após startup para todos os módulos carregarem
    while True:
        try:
            if should_send_summary():
                _dslog.info("[Server] Disparando resumo diário...")
                await asyncio.to_thread(send_daily_summary)
        except Exception as _e:
            _dslog.error(f"Daily summary loop error: {_e}")
        await asyncio.sleep(300)  # check a cada 5 min


@app.get("/api/daily-summary")
async def trigger_daily_summary():
    """Força envio do resumo diário no Telegram (para teste). Retorna resultado real."""
    try:
        from trinity.traders.daily_summary import force_send_summary
        result = await asyncio.to_thread(force_send_summary)
        if result and result.get("ok"):
            return JSONResponse({"status": "sent"})
        return JSONResponse({"status": "failed", "detail": result}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/altcoin-scanner")
def get_altcoin_scanner():
    """Último resultado do Altcoin Scanner SMC."""
    if ALTCOIN_SCAN_FILE.exists():
        try:
            data = json.loads(ALTCOIN_SCAN_FILE.read_text())
            # Remove NO_TRADE com score < 45 (abaixo do threshold de display)
            # Sinais LONG/SHORT aprovados sempre passam independente do score
            data["candidates"] = [
                c for c in data.get("candidates", [])
                if not (c.get("direction") == "NO_TRADE" and c.get("smc_score", 0) < 45)
            ]
            return JSONResponse(content=data)
        except Exception:
            pass
    return JSONResponse(content={"scan_ts": None, "candidates": [], "coins_scanned": 0})


# ── Aliases para o React frontend v3.0 ───────────────────────────────────────

@app.get("/api/current-state")
def get_current_state():
    """Alias de /api/status para o React frontend."""
    if STATE_FILE.exists():
        try:
            return JSONResponse(content=json.loads(STATE_FILE.read_text()))
        except Exception:
            pass
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


# ── Endpoints de Performance Real (Etapas 1 + 3) ─────────────────────────────

@app.get("/api/win-rate")
def get_win_rate():
    """
    Métricas de acerto real do bot — geradas automaticamente pelo OutcomeTracker.
    Campos: total_signals, wins, losses, neutral, win_rate_pct, win_rate_display,
            avg_score_wins, avg_score_losses, best_direction,
            by_conviction_tier, optimizer_progress.
    """
    if WIN_RATE_FILE.exists():
        try:
            return JSONResponse(content=json.loads(WIN_RATE_FILE.read_text()))
        except Exception:
            pass
    # Nunca gerado ainda — retorna estrutura vazia
    return JSONResponse(content={
        "updated_at": None, "total_signals": 0, "wins": 0, "losses": 0,
        "neutral": 0, "win_rate_pct": None, "win_rate_display": "Dados insuficientes",
        "avg_score_wins": None, "avg_score_losses": None, "best_direction": None,
        "by_conviction_tier": {
            "HIGH":   {"win_rate_pct": None, "count": 0},
            "MEDIUM": {"win_rate_pct": None, "count": 0},
        },
        "optimizer_progress": {
            "samples_collected": 0,
            "samples_needed":    30,
            "pct_complete":      0,
            "ready":             False,
        },
    })


@app.get("/api/seasonality-status")
def get_seasonality_status():
    """
    Estado atual do Seasonality Engine — multiplicadores para agora (UTC).
    Campos: current, data_available, generated_at, top_combinations.
    """
    try:
        from seasonality_engine import get_seasonality_engine as _get_sea
        _sea = _get_sea()
        return JSONResponse(content={
            "current":          _sea.get_multiplier(),
            "data_available":   _sea.data_available,
            "generated_at":     _sea.generated_at,
            "top_combinations": _sea.top_combinations,
        })
    except Exception as e:
        return JSONResponse(content={
            "current": {
                "day_multiplier": 1.0, "hour_multiplier": 1.0,
                "combined_multiplier": 1.0, "day_label": "—",
                "hour_label": "—", "context": "Dados não disponíveis",
                "data_available": False,
            },
            "data_available":   False,
            "generated_at":     None,
            "top_combinations": [],
            "error":            str(e),
        })


@app.get("/api/news-status")
def get_news_status():
    """
    Estado atual do News Sentinel — sentimento macro, lock ativo e últimas notícias.
    Campos: locked_until, lock_reason, current_sentiment, sentiment_score_adj,
            multiplier, last_high_impact, last_checked, active_news (últimas 5).
    """
    try:
        import sys as _sys
        _sys.path.insert(0, str(BASE_DIR))
        from news_sentinel import get_sentinel as _get_ns
        _ns    = _get_ns()
        _state = _ns.state_snapshot
        return JSONResponse(content={
            "locked":              _ns.is_locked(),
            "locked_until":        _state.get("locked_until"),
            "lock_reason":         _state.get("lock_reason"),
            "current_sentiment":   _state.get("current_sentiment", "NEUTRAL"),
            "sentiment_score_adj": _state.get("sentiment_score_adj", 0.0),
            "multiplier":          _ns.get_score_multiplier(),
            "last_high_impact":    _state.get("last_high_impact"),
            "last_checked":        _state.get("last_checked"),
            "active_news":         (_state.get("active_news") or [])[:5],
        })
    except Exception as e:
        return JSONResponse(content={
            "locked":            False,
            "current_sentiment": "NEUTRAL",
            "multiplier":        1.0,
            "error":             str(e),
        })


@app.get("/api/news-feed")
def get_news_feed():
    """
    Últimas 20 notícias HIGH e MEDIUM dos logs diários do News Sentinel.
    Retorna lista ordenada por timestamp desc.
    """
    import glob as _glob
    results = []
    try:
        files = sorted(
            _glob.glob(str(LOGS_DIR / "news_*.jsonl")), reverse=True
        )[:3]  # últimos 3 dias
        for filepath in files:
            try:
                with open(filepath) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        entry = json.loads(line)
                        if entry.get("magnitude") in ("HIGH", "MEDIUM"):
                            results.append(entry)
            except Exception:
                continue
        # Ordena por timestamp desc e limita a 20
        results.sort(key=lambda x: x.get("ts", ""), reverse=True)
        results = results[:20]
    except Exception as e:
        return JSONResponse(content={"error": str(e), "items": []})
    return JSONResponse(content={"items": results, "total": len(results)})


@app.get("/api/optimization-report")
def get_optimization_report():
    """
    Relatório de otimização de pesos — gerado pelo WeightOptimizer.
    Campos: current_weights, suggested_weights, weight_changes, correlation_by_layer,
            recommendation, samples_collected, samples_needed, safe_to_apply.
    """
    if OPT_REPORT_FILE.exists():
        try:
            return JSONResponse(content=json.loads(OPT_REPORT_FILE.read_text()))
        except Exception:
            pass
    return JSONResponse(content={
        "recommendation": "insufficient_data",
        "samples_collected": 0,
        "samples_needed": 30,
        "safe_to_apply": False,
    })


@app.post("/api/apply-weights")
async def apply_optimized_weights():
    """
    Aplica os pesos otimizados em smart_money_engine.py (com backup).
    Só funciona se safe_to_apply == True no último relatório.
    Retorna {applied: bool, error?: str}.
    """
    if not OPT_REPORT_FILE.exists():
        return JSONResponse(content={"applied": False, "error": "relatório não encontrado"}, status_code=404)

    try:
        report = json.loads(OPT_REPORT_FILE.read_text())
    except Exception as _e:
        return JSONResponse(content={"applied": False, "error": f"leitura do relatório: {_e}"}, status_code=500)

    if not report.get("safe_to_apply", False):
        return JSONResponse(
            content={
                "applied": False,
                "error": f"não seguro para aplicar — {report.get('recommendation')} "
                         f"({report.get('samples_collected', 0)}/{report.get('samples_needed', 30)} amostras)",
            },
            status_code=400,
        )

    weights = report.get("suggested_weights", {})
    if not weights:
        return JSONResponse(content={"applied": False, "error": "pesos sugeridos ausentes"}, status_code=400)

    try:
        import sys as _sys
        _sys.path.insert(0, str(BASE_DIR))
        from weight_optimizer import get_optimizer as _get_opt
        ok = await asyncio.to_thread(_get_opt().apply_weights, weights)
        if ok:
            return JSONResponse(content={"applied": True, "weights": weights})
        return JSONResponse(content={"applied": False, "error": "falha na escrita do engine (backup restaurado)"}, status_code=500)
    except Exception as _e:
        return JSONResponse(content={"applied": False, "error": str(_e)}, status_code=500)


REPORTS_DIR = BASE_DIR / "dashboard" / "reports"


@app.get("/api/macro-report")
async def get_macro_report():
    """Gera relatório macro semanal via Claude API, envia ao Telegram e retorna o PDF."""
    import logging as _log
    import requests as _requests
    _logger = _log.getLogger("dashboard.server")
    try:
        from trinity.macro_report import generate_macro_report
        from fastapi.responses import FileResponse
        from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

        pdf_path = await asyncio.to_thread(generate_macro_report)

        # Envia PDF ao Telegram via sendDocument
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            try:
                with open(pdf_path, "rb") as _f:
                    tg = _requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                        data={"chat_id": TELEGRAM_CHAT_ID, "caption": "📊 Relatório Macro Semanal — Trinity Trading"},
                        files={"document": (_f.name.split("/")[-1], _f, "application/pdf")},
                        timeout=30,
                    ).json()
                if not tg.get("ok"):
                    _logger.error(f"[MacroReport] Telegram recusou: {tg}")
                else:
                    _logger.info("[MacroReport] PDF enviado ao Telegram com sucesso")
            except Exception as _te:
                _logger.error(f"[MacroReport] Erro ao enviar ao Telegram: {_te}")

        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=Path(pdf_path).name,
        )
    except Exception as _e:
        _logger.error(f"[Server] Macro report error: {_e}")
        return JSONResponse(content={"error": str(_e)}, status_code=500)


@app.get("/api/macro-reports")
async def list_macro_reports():
    """Lista relatórios macro anteriores gerados."""
    try:
        if not REPORTS_DIR.exists():
            return JSONResponse(content={"reports": []})
        pdfs = sorted(REPORTS_DIR.glob("macro_*.pdf"), reverse=True)
        return JSONResponse(content={
            "reports": [
                {
                    "name":       p.name,
                    "size_kb":    round(p.stat().st_size / 1024),
                    "created_ts": p.stat().st_mtime,
                }
                for p in pdfs[:20]
            ]
        })
    except Exception:
        return JSONResponse(content={"reports": []})


# ── Multi-Exchange API — Trinity v5 ──────────────────────────────────────────

@app.get("/api/exchanges/status")
async def get_exchanges_status():
    """
    Status do ExchangeManager: exchanges ativas, contagem de tickers por exchange,
    total de símbolos únicos e símbolos em 2+ exchanges.
    """
    if _ex_mgr is None:
        return JSONResponse(content={"error": "ExchangeManager não disponível"}, status_code=503)
    try:
        summary = await asyncio.to_thread(_ex_mgr.status_summary)
        return JSONResponse(content=summary)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/funding-arbitrage")
async def get_funding_arbitrage(min_spread: float = 20.0):
    """
    Oportunidades de arbitragem de funding rate cross-exchange.
    Retorna lista ordenada por spread_annual_pct DESC.
    Parâmetro min_spread: mínimo anualizado % (default 20%).
    """
    if _ex_mgr is None:
        return JSONResponse(content={"error": "ExchangeManager não disponível", "opportunities": []}, status_code=503)
    try:
        opps = await asyncio.to_thread(_ex_mgr.get_funding_arbitrage_opportunities, min_spread)
        return JSONResponse(content={"opportunities": opps, "total": len(opps)})
    except Exception as e:
        return JSONResponse(content={"error": str(e), "opportunities": []}, status_code=500)


@app.get("/api/exchanges/ticker/{symbol}")
async def get_exchange_ticker(symbol: str):
    """
    Ticker de um símbolo em todas as exchanges disponíveis.
    Retorna lista com preço, funding rate, OI, volume por exchange.
    """
    if _ex_mgr is None:
        return JSONResponse(content={"error": "ExchangeManager não disponível"}, status_code=503)
    try:
        import dataclasses as _dc
        unified  = await asyncio.to_thread(_ex_mgr.fetch_all_tickers_unified)
        tickers  = unified.get(symbol.upper(), [])
        return JSONResponse(content={
            "symbol":    symbol.upper(),
            "exchanges": [_dc.asdict(t) for t in tickers],
            "total":     len(tickers),
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ── ML Pipeline ──────────────────────────────────────────────────────────────

_ml_mgr = None  # inicializado lazy no primeiro uso

def _get_ml_mgr():
    global _ml_mgr
    if _ml_mgr is None:
        from trinity.ml.ml_manager import MLManager
        _ml_mgr = MLManager.get_instance()
    return _ml_mgr


@app.get("/api/ml/run")
@app.post("/api/ml/run")
async def ml_run():
    """
    Dispara o pipeline de ML (Feature Importance + Weight Optimizer) em background.
    Retorna 202 se iniciou, 409 se já está rodando.
    Aceita GET e POST (curl simples sem Content-Type funciona com GET).
    """
    mgr = _get_ml_mgr()
    started = await asyncio.to_thread(mgr.run_async)
    if started:
        return JSONResponse(content={"status": "started"}, status_code=202)
    return JSONResponse(content={"status": "already_running"}, status_code=409)


@app.get("/api/ml/status")
def ml_status():
    """
    Retorna estado atual do pipeline ML e resultados resumidos para o dashboard.
    """
    try:
        mgr = _get_ml_mgr()
        return JSONResponse(content=mgr.summary_for_api())
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/ml/results")
def ml_results():
    """Retorna resultados completos do pipeline ML (feature importance + pesos)."""
    try:
        mgr = _get_ml_mgr()
        res = mgr.get_results()
        if not res:
            return JSONResponse(content={"status": "no_data"})
        return JSONResponse(content=res)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ── Outcomes export (backup antes de redeploy) ───────────────────────────────

@app.get("/api/outcomes/export")
async def api_outcomes_export():
    """
    Exporta todos os outcomes resolvidos como JSONL (uma linha por outcome).
    Usar antes de qualquer git push para não perder dados do Render:
      curl -s https://trinity-trading.onrender.com/api/outcomes/export > logs/outcomes_2026-MM.jsonl
      git add logs/outcomes_*.jsonl && git commit -m "backup outcomes" && git push
    """
    lines = []
    for f in sorted(glob.glob("logs/outcomes_*.jsonl")):
        try:
            with open(f) as fh:
                lines.extend(l.strip() for l in fh if l.strip())
        except Exception:
            pass
    return Response(
        content="\n".join(lines),
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=outcomes.jsonl"},
    )


# ── Exchange Health (Trinity v7) ──────────────────────────────────────────────

@app.get("/api/exchanges/health")
async def api_exchanges_health():
    """
    Health das exchanges: status, latência (ms), nº de contratos.
    Usa o cache interno do ExchangeManager (5s TTL) — custo zero quando quente.
    """
    if _ex_mgr is None:
        return JSONResponse(content={"exchanges": [], "ts": time.time()})
    try:
        import time as _t
        t0 = _t.time()
        unified = await asyncio.to_thread(_ex_mgr.fetch_all_tickers_unified)
        elapsed_ms = round((_t.time() - t0) * 1000, 1)

        per_ex: dict[str, int] = {}
        for tickers in unified.values():
            for tk in tickers:
                per_ex[tk.exchange] = per_ex.get(tk.exchange, 0) + 1

        result = []
        for name in _ex_mgr.active_exchanges:
            n = per_ex.get(name, 0)
            result.append({
                "name":   name,
                "status": "ok" if n > 0 else "error",
                "ms":     elapsed_ms,
                "n":      n,
            })
        return JSONResponse(content={"exchanges": result, "ts": _t.time()})
    except Exception as e:
        return JSONResponse(content={"exchanges": [], "ts": time.time(), "error": str(e)})


# ── Equity Curve (Trinity v7) ──────────────────────────────────────────────────

@app.get("/api/equity-curve")
async def api_equity_curve():
    """
    Equity curve acumulada baseada nos outcomes resolvidos.
    Retorna {curve: [{ts, pnl}], total: float, n: int}.
    P&L: usa pnl_pct se disponível; fallback WIN=+2.2%, LOSS=-1.5%.
    """
    try:
        from trinity.ml.feature_importance import _load_resolved_outcomes
        outcomes = await asyncio.to_thread(_load_resolved_outcomes)
        resolved = [o for o in outcomes if o.get("status") in ("WIN", "LOSS")]
        resolved.sort(key=lambda o: o.get("resolved_at", o.get("timestamp", "")))

        curve = []
        cum = 0.0
        for o in resolved:
            if "pnl_pct" in o:
                pnl = float(o["pnl_pct"])
            else:
                pnl = 2.2 if o["status"] == "WIN" else -1.5
            cum += pnl
            curve.append({
                "ts":  o.get("resolved_at", o.get("timestamp", "")),
                "pnl": round(cum, 2),
            })

        return JSONResponse(content={"curve": curve, "total": round(cum, 2), "n": len(curve)})
    except Exception as e:
        return JSONResponse(content={"curve": [], "total": 0.0, "n": 0, "error": str(e)})


# ── Calendar Heatmap (Trinity v7) ─────────────────────────────────────────────

@app.get("/api/calendar-heatmap")
async def api_calendar_heatmap():
    """
    Win rate diário para heatmap dos últimos 90 dias.
    Retorna {heatmap: [{d, w, l, n, wr}]}.
    """
    try:
        from trinity.ml.feature_importance import _load_resolved_outcomes
        outcomes = await asyncio.to_thread(_load_resolved_outcomes)
        resolved = [o for o in outcomes if o.get("status") in ("WIN", "LOSS")]

        by_day: dict[str, dict] = {}
        for o in resolved:
            ts = o.get("resolved_at", o.get("timestamp", ""))
            day = ts[:10] if ts else None
            if not day:
                continue
            if day not in by_day:
                by_day[day] = {"w": 0, "l": 0}
            if o["status"] == "WIN":
                by_day[day]["w"] += 1
            else:
                by_day[day]["l"] += 1

        heatmap = []
        for day, counts in sorted(by_day.items()):
            w, l = counts["w"], counts["l"]
            n = w + l
            wr = round(w / n * 100, 1) if n > 0 else 0.0
            heatmap.append({"d": day, "w": w, "l": l, "n": n, "wr": wr})

        return JSONResponse(content={"heatmap": heatmap})
    except Exception as e:
        return JSONResponse(content={"heatmap": [], "error": str(e)})


# ── Serve React app v3.0 em /app/ ────────────────────────────────────────────
REACT_APP_DIR = BASE_DIR / "dashboard" / "static" / "app"
if REACT_APP_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(REACT_APP_DIR), html=True), name="react-app")

# Serve frontend (legacy HTML) — deve vir POR ÚLTIMO
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
