"""
predictive_pump_trader.py — Orquestrador do Predictive Pump Trader (Cap. 20)

NÃO detecta pumps que já aconteceram.
PREDIZ pumps ANTES de acontecerem.

Pipeline a cada ciclo (30s):
  1. AltcoinMarketScanner.scan_universe()  → top 50 pump candidates (cache 5min)
  2. AltcoinMarketScanner.fetch_batch()    → dados completos em paralelo
  3. Para cada coin:
     a. WhaleAccumulationDetector
     b. ShortSqueezeDetector
     c. LiquidityGravityDetector
     d. BreakoutPressureDetector
     e. PumpPredictionModel (smart money)
     f. PumpScoringEngine → pump_score final
  4. Filtra top N por pump_score
  5. Salva em dashboard/pump_scan_latest.json
  6. Envia alerta Telegram se score >= ALERT_THRESHOLD

Threshold de alerta:
  WATCH  → score >= 50  (só dashboard)
  ALERT  → score >= 60  (Telegram leve)
  READY  → score >= 75  (alerta forte 🚀)
  LAUNCH → score >= 85  (alerta urgente 🚀🚀🚀)

Uso como módulo:
  from trinity.traders.predictive_pump_trader import run_pump_scan
  result = run_pump_scan()

Uso como loop background (chamado pelo dashboard/server.py):
  trader = PredictivePumpTrader()
  await trader.start_loop()
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SCAN_INTERVAL_S    = 30
TOP_RESULTS        = 15
ALERT_THRESHOLD    = 60
LAUNCH_THRESHOLD   = 85
BASE_DIR           = Path(__file__).parent.parent.parent.parent
SCAN_OUTPUT_FILE   = BASE_DIR / "dashboard" / "pump_scan_latest.json"

_alert_cooldown: dict = {}
ALERT_COOLDOWN_S   = 300
LAUNCH_COOLDOWN_S  = 150


# ── Dataclass de resultado por coin ───────────────────────────────────────────

@dataclass
class PumpCandidate:
    symbol:             str
    price:              float
    price_change_pct:   float
    pump_score:         float
    pump_probability:   str       # LOW/MEDIUM/HIGH/EXTREME
    urgency:            str       # WATCH/ALERT/READY/LAUNCH
    recommended_action: str
    signal_valid:       bool
    component_scores:   dict      # {whale, squeeze, gravity, breakout, smart_money}
    top_signals:        list
    funding_rate:       float
    long_short_ratio:   float
    oi_change_pct:      float
    pump_target:        float     # preço alvo estimado (gravidade de liquidez)
    volume_24h:         float
    scanned_at:         str


# ── Orquestrador ──────────────────────────────────────────────────────────────

class PredictivePumpTrader:
    """Orquestrador do Predictive Pump Trader. Thread-safe via asyncio.to_thread()."""

    def __init__(self):
        from trinity.traders.predictive_pump_trader.altcoin_market_scanner import (
            scan_universe, fetch_batch,
        )
        from trinity.traders.predictive_pump_trader.whale_accumulation_detector import (
            detect_whale_accumulation,
        )
        from trinity.traders.predictive_pump_trader.short_squeeze_detector import (
            detect_short_squeeze,
        )
        from trinity.traders.predictive_pump_trader.liquidity_gravity_detector import (
            detect_liquidity_gravity,
        )
        from trinity.traders.predictive_pump_trader.breakout_pressure_detector import (
            detect_breakout_pressure,
        )
        from trinity.traders.predictive_pump_trader.pump_prediction_model import (
            predict_pump,
        )
        from trinity.traders.predictive_pump_trader.pump_scoring_engine import (
            score_pump,
        )

        self._scan_universe   = scan_universe
        self._fetch_batch     = fetch_batch
        self._detect_whale    = detect_whale_accumulation
        self._detect_squeeze  = detect_short_squeeze
        self._detect_gravity  = detect_liquidity_gravity
        self._detect_breakout = detect_breakout_pressure
        self._predict_pump    = predict_pump
        self._score_pump      = score_pump

    def run_scan(self) -> dict:
        """
        Executa um ciclo completo de scan.

        Retorna:
          {
            "scan_ts":         ISO timestamp,
            "scan_duration_s": float,
            "coins_scanned":   int,
            "candidates":      List[PumpCandidate as dict],
          }
        """
        t0      = time.time()
        scan_ts = _iso_now()

        hot_candidates = self._scan_universe()
        if not hot_candidates:
            log.warning("[PumpTrader] Universo vazio — scan abortado")
            return _empty_scan(scan_ts)

        symbols = [c["symbol"] for c in hot_candidates]
        log.info(f"[PumpTrader] Iniciando scan de {len(symbols)} candidatos...")

        coin_data_list = self._fetch_batch(symbols)
        log.info(f"[PumpTrader] {len(coin_data_list)}/{len(symbols)} coins com dados")

        results: List[PumpCandidate] = []
        for coin_data in coin_data_list:
            try:
                candidate = self._analyze_coin(coin_data, scan_ts)
                if candidate:
                    results.append(candidate)
            except Exception as e:
                log.warning(f"[PumpTrader] Erro ao analisar {coin_data.get('symbol', '?')}: {e}")

        results.sort(key=lambda c: c.pump_score, reverse=True)
        top = results[:TOP_RESULTS]

        duration = round(time.time() - t0, 2)
        log.info(
            f"[PumpTrader] Scan concluído em {duration}s — "
            f"top: {[(c.symbol, c.pump_score) for c in top[:3]]}"
        )

        return {
            "scan_ts":         scan_ts,
            "scan_duration_s": duration,
            "coins_scanned":   len(coin_data_list),
            "candidates":      [asdict(c) for c in top],
        }

    def _analyze_coin(self, coin_data: dict, scan_ts: str) -> Optional[PumpCandidate]:
        symbol = coin_data.get("symbol", "?")

        whale_result    = self._detect_whale(coin_data)
        squeeze_result  = self._detect_squeeze(coin_data)
        gravity_result  = self._detect_gravity(coin_data)
        breakout_result = self._detect_breakout(coin_data)
        sm_result       = self._predict_pump(coin_data)

        score_result = self._score_pump(
            whale_result, squeeze_result, gravity_result,
            breakout_result, sm_result,
        )

        pump_score = score_result["pump_score"]

        return PumpCandidate(
            symbol             = symbol,
            price              = coin_data.get("price", 0),
            price_change_pct   = coin_data.get("price_change_pct", 0),
            pump_score         = pump_score,
            pump_probability   = score_result["pump_probability"],
            urgency            = score_result["urgency"],
            recommended_action = score_result["recommended_action"],
            signal_valid       = score_result["signal_valid"],
            component_scores   = score_result["component_scores"],
            top_signals        = score_result["top_signals"],
            funding_rate       = coin_data.get("funding_rate", 0),
            long_short_ratio   = coin_data.get("long_short_ratio", 1.0),
            oi_change_pct      = coin_data.get("oi_change_pct", 0),
            pump_target        = score_result.get("pump_target", 0),
            volume_24h         = coin_data.get("volume_24h", 0),
            scanned_at         = scan_ts,
        )

    async def start_loop(self):
        """Loop assíncrono — roda em background como asyncio Task."""
        log.info(f"[PumpTrader] Loop iniciado (ciclo: {SCAN_INTERVAL_S}s)")
        while True:
            try:
                result = await asyncio.to_thread(self.run_scan)
                _save_result(result)
                await _send_telegram_alerts(result["candidates"])
            except Exception as e:
                log.error(f"[PumpTrader] Erro no loop: {e}")
            await asyncio.sleep(SCAN_INTERVAL_S)


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[PredictivePumpTrader] = None


def run_pump_scan() -> dict:
    """Execução única de scan (blocking). Singleton para reutilização."""
    global _instance
    if _instance is None:
        _instance = PredictivePumpTrader()
    return _instance.run_scan()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_result(result: dict):
    try:
        SCAN_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCAN_OUTPUT_FILE.write_text(json.dumps(result, indent=2, default=str))
    except Exception as e:
        log.warning(f"[PumpTrader] Erro ao salvar resultado: {e}")


async def _send_telegram_alerts(candidates: list):
    now     = time.time()
    alerted = 0

    for c in candidates:
        score  = c.get("pump_score", 0) if isinstance(c, dict) else c.pump_score
        symbol = c.get("symbol", "")   if isinstance(c, dict) else c.symbol

        if score < ALERT_THRESHOLD:
            break

        last_alert = _alert_cooldown.get(symbol, 0)
        cooldown   = LAUNCH_COOLDOWN_S if score >= LAUNCH_THRESHOLD else ALERT_COOLDOWN_S
        if (now - last_alert) < cooldown:
            continue

        try:
            await asyncio.to_thread(_send_pump_telegram, c if isinstance(c, dict) else asdict(c))
            _alert_cooldown[symbol] = now
            alerted += 1
        except Exception as e:
            log.warning(f"[PumpTrader] Telegram falhou para {symbol}: {e}")

    if alerted:
        log.info(f"[PumpTrader] {alerted} alertas Telegram enviados")


def _send_pump_telegram(c: dict):
    """
    Formata e envia alerta de pump para o Telegram.

    Formato:
    🚀 PUMP RADAR — PREDIÇÃO ANTECIPADA

    🪙 SOLUSDT  |  -2.1%  |  $145.80
    ⚡ READY  → pump_score: 76/100

    📊 Componentes:
      Whale:       82 | Squeeze:    71
      Gravidade:   65 | Breakout:   58
      Smart Money: 79

    🔑 Sinais:
    • Shorts presos: L/S 0.65x | Funding -120% a.a.
    • Bid wall forte — suporte sólido como rampa
    • Compressão ativa (8 velas) — breakout iminente

    🎯 Alvo: $153.20 (+5.1%)
    💡 LONG imediato — pump iniciando
    """
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
        import requests as _req

        symbol     = c.get("symbol", "?")
        price      = c.get("price", 0)
        pct_change = c.get("price_change_pct", 0)
        score      = c.get("pump_score", 0)
        urgency    = c.get("urgency", "WATCH")
        action     = c.get("recommended_action", "—")
        comp       = c.get("component_scores", {})
        signals    = c.get("top_signals", [])
        target     = c.get("pump_target", 0)
        funding    = c.get("funding_rate", 0)
        ls_ratio   = c.get("long_short_ratio", 1.0)

        urgency_emoji = {
            "LAUNCH": "🚀",
            "READY":  "⚡",
            "ALERT":  "📡",
            "WATCH":  "👁",
        }.get(urgency, "⚡")

        pct_str   = f"+{pct_change:.1f}%" if pct_change >= 0 else f"{pct_change:.1f}%"
        price_str = f"${price:,.4f}" if price < 10 else f"${price:,.2f}"

        signals_text = "\n".join(f"• {s}" for s in signals[:4]) if signals else "• Múltiplos sinais alinhados"

        target_str = ""
        if target and price:
            upside = (target - price) / price * 100
            t_price = f"${target:,.4f}" if target < 10 else f"${target:,.2f}"
            target_str = f"\n🎯 Alvo: {t_price}  (+{upside:.1f}%)"

        msg = (
            f"🚀 *PUMP RADAR — PREDIÇÃO ANTECIPADA*\n\n"
            f"🪙 *{symbol}*  |  {pct_str}  |  {price_str}\n"
            f"{urgency_emoji} *{urgency}*  →  pump\\_score: *{score:.0f}/100*\n\n"
            f"📊 *Componentes:*\n"
            f"  Whale: `{comp.get('whale', 0):.0f}` | Squeeze: `{comp.get('squeeze', 0):.0f}`\n"
            f"  Gravidade: `{comp.get('gravity', 0):.0f}` | Breakout: `{comp.get('breakout', 0):.0f}`\n"
            f"  Smart Money: `{comp.get('smart_money', 0):.0f}`\n\n"
            f"🔑 *Sinais:*\n{signals_text}"
            f"{target_str}\n\n"
            f"📈 L/S ratio: `{ls_ratio:.2f}x`  |  Funding: `{funding*100:.4f}%`\n"
            f"💡 _{action}_"
        )

        _req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       msg,
                "parse_mode": "Markdown",
            },
            timeout=8,
        )
    except Exception as e:
        log.warning(f"[PumpTrader] Telegram error: {e}")


def _empty_scan(scan_ts: str) -> dict:
    return {
        "scan_ts":         scan_ts,
        "scan_duration_s": 0.0,
        "coins_scanned":   0,
        "candidates":      [],
    }


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
