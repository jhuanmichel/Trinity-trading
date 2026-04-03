"""
btc_liquidation_engine.py — Real-time Binance BTC Liquidation Engine
WebSocket assíncrono, janelas rolling 1m/5m/15m, thread-safe.
Funciona em background thread — compatível com start.py (sync) e server.py (async).
"""
import asyncio
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    log.warning("websockets não instalado. Execute: pip install websockets")

# ─── Constantes ───────────────────────────────────────────────────────────────
WS_URL         = "wss://fstream.binance.com/ws/btcusdt@forceOrder"
SYMBOL         = "BTCUSDT"
WINDOW_SECONDS = {"1m": 60, "5m": 300, "15m": 900}
MAX_DEQUE_LEN  = 8_000      # ~15min de liquidações em mercado muito ativo
RECONNECT_BASE = 2.0        # backoff inicial (dobra a cada falha)
RECONNECT_MAX  = 60.0       # máximo entre tentativas
ALERT_MIN_USD  = 500_000    # loga liquidações ≥ $500k

# ─── Dataclass imutável por evento ────────────────────────────────────────────
@dataclass(frozen=True)
class LiqEvent:
    ts:        float   # Unix timestamp
    side:      str     # "LONG" (SELL order liquidada) | "SHORT" (BUY liquidada)
    usd_value: float   # ap * z  (USD)
    price:     float   # preço médio de execução
    qty:       float   # quantidade BTC


# ─── Estado global ────────────────────────────────────────────────────────────
_events: deque        = deque(maxlen=MAX_DEQUE_LEN)
_events_lock          = threading.Lock()
_snapshot_lock        = threading.Lock()

_snapshot: dict = {
    "symbol":               SYMBOL,
    "long_liquidations":    0.0,
    "short_liquidations":   0.0,
    "total_liquidations":   0.0,
    "liquidation_bias":     "NEUTRAL",
    "liquidation_strength": 0,
    "windows": {
        "1m":  {"long": 0.0, "short": 0.0, "total": 0.0},
        "5m":  {"long": 0.0, "short": 0.0, "total": 0.0},
        "15m": {"long": 0.0, "short": 0.0, "total": 0.0},
    },
    "last_event_ts":  0.0,
    "last_event_usd": 0.0,
    "last_event_side": "",
    "connected":      False,
    "event_count":    0,
}

_loop:   Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread]          = None
_running = False


# ─── Cálculos (chamados dentro do loop asyncio) ───────────────────────────────
def _calc_windows() -> dict:
    """
    Agrega eventos por janela (1m/5m/15m).
    Itera do mais recente para o mais antigo — para quando sai da janela.
    Retorna valores em milhões de USD.
    """
    now = time.time()
    result: dict = {}

    with _events_lock:
        events_snapshot = list(_events)   # cópia para não bloquear deque

    for label, secs in WINDOW_SECONDS.items():
        cutoff  = now - secs
        long_v  = 0.0
        short_v = 0.0
        for ev in reversed(events_snapshot):
            if ev.ts < cutoff:
                break
            if ev.side == "LONG":
                long_v  += ev.usd_value
            else:
                short_v += ev.usd_value

        total = long_v + short_v
        result[label] = {
            "long":  round(long_v  / 1_000_000, 4),
            "short": round(short_v / 1_000_000, 4),
            "total": round(total   / 1_000_000, 4),
        }

    return result


def _calc_bias_strength(windows: dict, ref_window: str = "5m") -> tuple[str, int]:
    """
    Bias = "LONG"  se short_liq > long_liq  (shorts sofrendo = mercado sobe)
    Bias = "SHORT" se long_liq  > short_liq (longs sofrendo  = mercado cai)
    Strength = |short - long| / total * 100
    """
    w     = windows.get(ref_window, {})
    long  = w.get("long",  0.0)
    short = w.get("short", 0.0)
    total = long + short

    if total < 0.01:               # < $10k → sem dados suficientes
        return "NEUTRAL", 0

    if short > long:
        bias = "LONG"
    elif long > short:
        bias = "SHORT"
    else:
        bias = "NEUTRAL"

    strength = min(100, int(abs(short - long) / total * 100))
    return bias, strength


def _rebuild_snapshot(ev: LiqEvent) -> None:
    """Recalcula snapshot e atualiza com lock (chamado a cada evento)."""
    windows           = _calc_windows()
    bias, strength    = _calc_bias_strength(windows, ref_window="5m")
    w15               = windows.get("15m", {})

    with _snapshot_lock:
        _snapshot["windows"]               = windows
        _snapshot["long_liquidations"]     = w15.get("long",  0.0)
        _snapshot["short_liquidations"]    = w15.get("short", 0.0)
        _snapshot["total_liquidations"]    = w15.get("total", 0.0)
        _snapshot["liquidation_bias"]      = bias
        _snapshot["liquidation_strength"]  = strength
        _snapshot["last_event_ts"]         = ev.ts
        _snapshot["last_event_usd"]        = round(ev.usd_value / 1_000_000, 4)
        _snapshot["last_event_side"]       = ev.side
        _snapshot["event_count"]           = len(_events)


# ─── Parser de evento Binance ─────────────────────────────────────────────────
def _parse_and_store(raw: str) -> Optional[LiqEvent]:
    """
    Parseia forceOrder da Binance e armazena no deque.

    Payload Binance:
    {
      "e": "forceOrder",
      "E": <event_time_ms>,
      "o": {
        "s": "BTCUSDT",
        "S": "SELL",      ← "SELL" = LONG liquidado | "BUY" = SHORT liquidado
        "ap": "67000",    ← average price
        "z": "0.015",     ← filled qty (BTC)
        "T": <trade_time_ms>
      }
    }
    """
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if msg.get("e") != "forceOrder":
        return None

    order     = msg.get("o", {})
    side_raw  = order.get("S", "")
    ap        = float(order.get("ap", 0) or 0)
    qty       = float(order.get("z",  0) or 0)
    ts_ms     = int(order.get("T",  0) or msg.get("E", 0))

    if ap <= 0 or qty <= 0:
        return None

    usd_value = ap * qty
    side      = "LONG" if side_raw == "SELL" else "SHORT"
    ts        = ts_ms / 1000.0

    ev = LiqEvent(ts=ts, side=side, usd_value=usd_value, price=ap, qty=qty)

    with _events_lock:
        _events.append(ev)

    with _snapshot_lock:
        _snapshot["connected"]  = True

    return ev


# ─── WebSocket listener (asyncio) ─────────────────────────────────────────────
async def _ws_listener() -> None:
    """Loop principal — conecta, escuta e reconecta com backoff exponencial."""
    attempt = 0

    while True:
        try:
            log.info(f"🔌 Conectando WebSocket Binance liquidações: {WS_URL}")

            async with websockets.connect(
                WS_URL,
                ping_interval = 20,
                ping_timeout  = 10,
                close_timeout = 5,
                max_size      = 2 ** 20,   # 1MB
            ) as ws:
                attempt = 0    # reset backoff após conexão bem-sucedida

                with _snapshot_lock:
                    _snapshot["connected"] = True

                log.info("✅ WebSocket Binance conectado — escutando liquidações BTCUSDT")

                async for raw in ws:
                    ev = _parse_and_store(raw)
                    if ev:
                        _rebuild_snapshot(ev)
                        if ev.usd_value >= ALERT_MIN_USD:
                            log.info(
                                f"   💥 LIQ {ev.side:5s} "
                                f"${ev.usd_value/1_000_000:.2f}M "
                                f"@ ${ev.price:,.0f}"
                            )

        except Exception as e:
            with _snapshot_lock:
                _snapshot["connected"] = False

            delay = min(RECONNECT_BASE * (2 ** attempt), RECONNECT_MAX)
            log.warning(f"⚠️ WS desconectado ({type(e).__name__}: {e}) — reconectando em {delay:.0f}s")
            attempt += 1
            await asyncio.sleep(delay)


# ─── API pública (thread-safe) ────────────────────────────────────────────────
def get_snapshot() -> dict:
    """Retorna cópia atômica do snapshot. Thread-safe."""
    with _snapshot_lock:
        return {
            "symbol":               _snapshot["symbol"],
            "long_liquidations":    _snapshot["long_liquidations"],
            "short_liquidations":   _snapshot["short_liquidations"],
            "total_liquidations":   _snapshot["total_liquidations"],
            "liquidation_bias":     _snapshot["liquidation_bias"],
            "liquidation_strength": _snapshot["liquidation_strength"],
            "windows":              {k: dict(v) for k, v in _snapshot["windows"].items()},
            "connected":            _snapshot["connected"],
            "event_count":          _snapshot["event_count"],
            "last_event_ts":        _snapshot["last_event_ts"],
            "last_event_usd":       _snapshot["last_event_usd"],
            "last_event_side":      _snapshot["last_event_side"],
        }


def get_dashboard_section() -> dict:
    """Seção compacta para current_state.json e dashboard."""
    s = get_snapshot()
    return {
        "bias":      s["liquidation_bias"],
        "strength":  s["liquidation_strength"],
        "long_liq":  s["long_liquidations"],
        "short_liq": s["short_liquidations"],
        "total":     s["total_liquidations"],
        "connected": s["connected"],
        "windows":   s["windows"],
    }


def get_for_scoring() -> dict:
    """
    Retorna dict compatível com o scoring institucional.
    Bias LONG = bullish (shorts sendo liquidados).
    """
    s = get_snapshot()
    bias     = s["liquidation_bias"]
    strength = s["liquidation_strength"]
    total    = s["total_liquidations"]

    # Score 0-100 para uso em institutional_scoring
    if bias == "LONG":
        score = 50 + min(45, strength // 2)
    elif bias == "SHORT":
        score = 50 - min(45, strength // 2)
    else:
        score = 50

    return {
        "liq_bias":     bias,
        "liq_strength": strength,
        "liq_score":    score,
        "liq_long_usd": s["long_liquidations"],
        "liq_short_usd": s["short_liquidations"],
        "liq_total_usd": s["total_liquidations"],
        "liq_connected": s["connected"],
        "liq_windows":   s["windows"],
        "summary":       (
            f"Liq bias {bias} (força {strength}%) | "
            f"Long ${s['long_liquidations']:.2f}M / "
            f"Short ${s['short_liquidations']:.2f}M (15m)"
        ),
    }


def is_connected() -> bool:
    with _snapshot_lock:
        return bool(_snapshot["connected"])


# ─── Inicialização ────────────────────────────────────────────────────────────
def start_background() -> None:
    """
    Inicia o engine em uma thread de background com próprio event loop.
    Usar em start.py (contexto síncrono).
    """
    global _loop, _thread, _running

    if not _WS_AVAILABLE:
        log.warning("websockets não instalado — liquidation engine desabilitado")
        return

    if _running and _thread and _thread.is_alive():
        log.debug("Liquidation engine já está rodando")
        return

    def _run():
        global _loop, _running
        _loop    = asyncio.new_event_loop()
        _running = True
        asyncio.set_event_loop(_loop)
        try:
            _loop.run_until_complete(_ws_listener())
        except Exception as e:
            log.error(f"Liquidation engine encerrado inesperadamente: {e}")
        finally:
            _running = False
            _loop.close()

    _thread = threading.Thread(target=_run, daemon=True, name="btc-liq-engine")
    _thread.start()
    log.info("🚀 BTC Liquidation Engine iniciado em background thread")


async def start_async() -> None:
    """
    Inicia o engine como coroutine asyncio.
    Usar em server.py: asyncio.create_task(start_async())
    """
    if not _WS_AVAILABLE:
        log.warning("websockets não instalado — liquidation engine desabilitado")
        return

    with _snapshot_lock:
        global _running
        _running = True

    await _ws_listener()
