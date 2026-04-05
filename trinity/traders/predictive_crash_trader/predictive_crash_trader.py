"""
predictive_crash_trader.py — Orquestrador do Predictive Crash Trader (Cap. 19)

NÃO detecta crashes que já aconteceram.
PREDIZ crashes ANTES de acontecerem.

Pipeline a cada ciclo (30s):
  1. AltcoinMarketScanner.scan_universe()  → top 50 candidatos (cache 5min)
  2. AltcoinMarketScanner.fetch_batch()    → dados completos em paralelo
  3. Para cada coin:
     a. LiquidityCollapseDetector
     b. LeveragePressureDetector
     c. WhaleDumpDetector
     d. VolatilityCompressionDetector
     e. CascadePredictionModel
     f. CrashScoringEngine → crash_score final
  4. Filtra top N por crash_score
  5. Salva em dashboard/crash_scan_latest.json
  6. Envia alerta Telegram se score >= ALERT_THRESHOLD

Threshold de alerta:
  WATCH    → score >= 50  (sem alerta Telegram, só dashboard)
  ALERT    → score >= 60  (alerta Telegram leve)
  DANGER   → score >= 75  (alerta forte)
  CRITICAL → score >= 85  (alerta urgente repetido)

Uso como módulo:
  from trinity.traders.predictive_crash_trader import run_crash_scan
  result = run_crash_scan()   # execução única

Uso como loop background (chamado pelo dashboard/server.py):
  trader = PredictiveCrashTrader()
  await trader.start_loop()   # asyncio task
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
SCAN_INTERVAL_S      = 30       # ciclo de scan em segundos
TOP_RESULTS          = 5        # top N oportunidades institucionais
OPP_THRESHOLD        = 70       # opportunity_score mínimo para incluir
ALERT_THRESHOLD      = 70       # opp_score mínimo para alerta Telegram
CRITICAL_THRESHOLD   = 88       # opp_score para alerta crítico repetido
BASE_DIR             = Path(__file__).parent.parent.parent.parent  # raiz do projeto
SCAN_OUTPUT_FILE     = BASE_DIR / "dashboard" / "crash_scan_latest.json"

# Cooldown de alertas Telegram por símbolo (evita spam)
_alert_cooldown: dict = {}       # {symbol: last_alert_ts}
ALERT_COOLDOWN_S     = 300       # 5min entre alertas do mesmo símbolo


# ── Dataclass de resultado por coin ───────────────────────────────────────────

@dataclass
class CrashCandidate:
    symbol:              str
    price:               float
    price_change_pct:    float
    crash_score:         float
    crash_probability:   str       # LOW/MEDIUM/HIGH/EXTREME
    urgency:             str       # WATCH/ALERT/DANGER/CRITICAL
    recommended_action:  str
    signal_valid:        bool
    component_scores:    dict      # {liquidity, leverage, whale, compression, funding_oi, cascade}
    top_signals:         list
    funding_rate:        float
    long_short_ratio:    float
    oi_change_pct:       float
    estimated_drawdown:  float     # % drawdown esperado pela cascata
    cascade_target:      float     # preço alvo se cascade
    volume_24h:          float
    scanned_at:          str       # ISO timestamp
    # Institutional Volatility Engine fields
    expected_move_pct:   float     # movimento esperado em %
    move_classification: str       # MICRO/WEAK/TRADEABLE/STRONG/EXTREME
    opportunity_score:   float     # score composto 0-100


# ── Orquestrador ──────────────────────────────────────────────────────────────

class PredictiveCrashTrader:
    """
    Orquestrador principal do Predictive Crash Trader.

    Thread-safe para uso com asyncio via asyncio.to_thread().
    """

    def __init__(self):
        # Lazy imports para evitar circular imports no startup
        from trinity.traders.predictive_crash_trader.altcoin_market_scanner import (
            scan_universe, fetch_batch,
        )
        from trinity.traders.predictive_crash_trader.liquidity_collapse_detector import (
            detect_liquidity_collapse,
        )
        from trinity.traders.predictive_crash_trader.leverage_pressure_detector import (
            detect_leverage_pressure,
        )
        from trinity.traders.predictive_crash_trader.whale_dump_detector import (
            detect_whale_dump,
        )
        from trinity.traders.predictive_crash_trader.volatility_compression_detector import (
            detect_volatility_compression,
        )
        from trinity.traders.predictive_crash_trader.cascade_prediction_model import (
            predict_cascade,
        )
        from trinity.traders.predictive_crash_trader.crash_scoring_engine import (
            score_crash,
        )

        self._scan_universe    = scan_universe
        self._fetch_batch      = fetch_batch
        self._detect_liq       = detect_liquidity_collapse
        self._detect_lev       = detect_leverage_pressure
        self._detect_whale     = detect_whale_dump
        self._detect_vol       = detect_volatility_compression
        self._predict_cascade  = predict_cascade
        self._score_crash      = score_crash

    def run_scan(self) -> dict:
        """
        Executa um ciclo completo de scan.

        Retorna:
          {
            "scan_ts":      ISO timestamp,
            "scan_duration_s": float,
            "coins_scanned":   int,
            "candidates":      List[CrashCandidate as dict],
          }
        """
        t0 = time.time()
        scan_ts = _iso_now()

        # 1. Universo → top 50 hot candidates
        hot_candidates = self._scan_universe()
        if not hot_candidates:
            log.warning("[CrashTrader] Universo vazio — scan abortado")
            return _empty_scan(scan_ts)

        symbols = [c["symbol"] for c in hot_candidates]
        log.info(f"[CrashTrader] Iniciando scan de {len(symbols)} candidatos...")

        # 2. Deep data em paralelo
        coin_data_list = self._fetch_batch(symbols)
        log.info(f"[CrashTrader] {len(coin_data_list)}/{len(symbols)} coins com dados completos")

        # 3. Análise de cada coin
        results: List[CrashCandidate] = []
        for coin_data in coin_data_list:
            try:
                candidate = self._analyze_coin(coin_data, scan_ts)
                if candidate:
                    results.append(candidate)
            except Exception as e:
                log.warning(f"[CrashTrader] Erro ao analisar {coin_data.get('symbol', '?')}: {e}")

        # 4. Ordena por opportunity_score desc, filtra por OPP_THRESHOLD
        results.sort(key=lambda c: c.opportunity_score, reverse=True)
        qualified = [c for c in results if c.opportunity_score >= OPP_THRESHOLD]
        top = qualified[:TOP_RESULTS]

        duration = round(time.time() - t0, 2)
        log.info(
            f"[CrashTrader] Scan concluído em {duration}s — "
            f"{len(qualified)} oportunidades >= {OPP_THRESHOLD} | "
            f"top: {[(c.symbol, round(c.opportunity_score), c.move_classification) for c in top[:3]]}"
        )

        return {
            "scan_ts":         scan_ts,
            "scan_duration_s": duration,
            "coins_scanned":   len(coin_data_list),
            "candidates":      [asdict(c) for c in top],
        }

    def _analyze_coin(self, coin_data: dict, scan_ts: str) -> Optional[CrashCandidate]:
        """Executa pipeline completo em um único coin."""
        symbol = coin_data.get("symbol", "?")

        # Pipeline de detectores
        liq_result  = self._detect_liq(coin_data)
        lev_result  = self._detect_lev(coin_data)
        whale_result = self._detect_whale(coin_data)
        vol_result  = self._detect_vol(coin_data)

        # Injeta resultado de leverage no cascade para usar cascade_probability
        coin_data["_leverage_result"] = lev_result
        cas_result = self._predict_cascade(coin_data)

        # Score final — passa coin_data para o Expected Move Model
        score_result = self._score_crash(
            liq_result, lev_result, whale_result, vol_result, cas_result, coin_data
        )

        crash_score = score_result["crash_score"]

        return CrashCandidate(
            symbol              = symbol,
            price               = coin_data.get("price", 0),
            price_change_pct    = coin_data.get("price_change_pct", 0),
            crash_score         = crash_score,
            crash_probability   = score_result["crash_probability"],
            urgency             = score_result["urgency"],
            recommended_action  = score_result["recommended_action"],
            signal_valid        = score_result["signal_valid"],
            component_scores    = score_result["component_scores"],
            top_signals         = score_result["top_signals"],
            funding_rate        = coin_data.get("funding_rate", 0),
            long_short_ratio    = coin_data.get("long_short_ratio", 1.0),
            oi_change_pct       = coin_data.get("oi_change_pct", 0),
            estimated_drawdown  = cas_result.get("estimated_drawdown", 0),
            cascade_target      = cas_result.get("cascade_target", 0),
            volume_24h          = coin_data.get("volume_24h", 0),
            scanned_at          = scan_ts,
            expected_move_pct   = score_result.get("expected_move_pct", 0.0),
            move_classification = score_result.get("move_classification", "MICRO"),
            opportunity_score   = score_result.get("opportunity_score", 0.0),
        )

    async def start_loop(self):
        """Loop assíncrono — roda em background como asyncio Task."""
        log.info(f"[CrashTrader] Loop iniciado (ciclo: {SCAN_INTERVAL_S}s)")
        while True:
            try:
                result = await asyncio.to_thread(self.run_scan)
                _save_result(result)
                await _send_telegram_alerts(result["candidates"])
            except Exception as e:
                log.error(f"[CrashTrader] Erro no loop: {e}")
            await asyncio.sleep(SCAN_INTERVAL_S)


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[PredictiveCrashTrader] = None


def run_crash_scan() -> dict:
    """
    Execução única de scan (blocking).
    Usa singleton para não recriar objetos a cada chamada.
    """
    global _instance
    if _instance is None:
        _instance = PredictiveCrashTrader()
    return _instance.run_scan()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_result(result: dict):
    """Salva resultado do scan em crash_scan_latest.json."""
    try:
        SCAN_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCAN_OUTPUT_FILE.write_text(json.dumps(result, indent=2, default=str))
    except Exception as e:
        log.warning(f"[CrashTrader] Erro ao salvar resultado: {e}")


async def _send_telegram_alerts(candidates: list):
    """
    Envia alertas Telegram para os candidatos com crash_score >= ALERT_THRESHOLD.
    Respeita cooldown por símbolo para evitar spam.
    """
    now = time.time()
    alerted = 0

    for c in candidates:
        score  = c.get("opportunity_score", 0) if isinstance(c, dict) else c.opportunity_score
        symbol = c.get("symbol", "") if isinstance(c, dict) else c.symbol

        if score < ALERT_THRESHOLD:
            break  # lista ordenada por opp_score — para quando abaixo do threshold

        last_alert = _alert_cooldown.get(symbol, 0)
        cooldown   = ALERT_COOLDOWN_S if score < CRITICAL_THRESHOLD else ALERT_COOLDOWN_S // 2
        if (now - last_alert) < cooldown:
            continue

        try:
            await asyncio.to_thread(_send_crash_telegram, c if isinstance(c, dict) else asdict(c))
            _alert_cooldown[symbol] = now
            alerted += 1
        except Exception as e:
            log.warning(f"[CrashTrader] Telegram falhou para {symbol}: {e}")

    if alerted:
        log.info(f"[CrashTrader] {alerted} alertas Telegram enviados")


def _send_crash_telegram(c: dict):
    """
    Formata e envia alerta de crash para o Telegram.

    Formato:
    🚨 CRASH RADAR — PREDIÇÃO ANTECIPADA

    🪙 SOLUSDT  |  -4.2%  |  $145.80
    ⚠️ DANGER  → crash_score: 78/100

    📊 Componentes:
      Liquidez:    82 | Alavancagem: 71
      Whale Dump:  65 | Compressão:  58
      Funding/OI:  79 | Cascata:     74

    🔑 Sinais:
    • OI spike +12% — armadilha de longs
    • Funding 320% a.a. — mercado sobrecarregado
    • Cascata projetada: -8.5% se stops ativados

    🎯 Alvo de cascata: $133.50 (-8.2%)
    💡 SHORT imediato — cascata iminente
    """
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
        import requests as _req

        symbol     = c.get("symbol", "?")
        price      = c.get("price", 0)
        pct_change = c.get("price_change_pct", 0)
        opp_score  = c.get("opportunity_score", 0)
        move_pct   = c.get("expected_move_pct", 0)
        move_cls   = c.get("move_classification", "WEAK")
        action     = c.get("recommended_action", "—")
        comp       = c.get("component_scores", {})
        signals    = c.get("top_signals", [])
        dd         = c.get("estimated_drawdown", 0)
        target     = c.get("cascade_target", 0)
        funding    = c.get("funding_rate", 0)
        ls_ratio   = c.get("long_short_ratio", 1.0)

        cls_emoji = {"EXTREME": "🔴", "STRONG": "🟠", "TRADEABLE": "🟡", "WEAK": "⚪"}.get(move_cls, "⚡")

        pct_str   = f"+{pct_change:.1f}%" if pct_change >= 0 else f"{pct_change:.1f}%"
        price_str = f"${price:,.4f}" if price < 10 else f"${price:,.2f}"

        signals_text = "\n".join(f"• {s}" for s in signals[:3]) if signals else "• Múltiplos sinais alinhados"

        target_str = ""
        if target and dd:
            t_price    = f"${target:,.4f}" if target < 10 else f"${target:,.2f}"
            target_str = f"\n🎯 Alvo cascata: {t_price}  (-{dd:.1f}%)"

        msg = (
            f"🚨 *CRASH RADAR — OPORTUNIDADE INSTITUCIONAL*\n\n"
            f"{cls_emoji} *{move_cls} OPPORTUNITY* — DOWN ↓\n"
            f"🪙 *{symbol}*  |  {pct_str}  |  {price_str}\n\n"
            f"📉 *Movimento esperado: -{move_pct:.1f}%*\n"
            f"⚡ Opportunity Score: *{opp_score:.0f}/100*\n\n"
            f"📊 *Componentes:*\n"
            f"  Liquidez: `{comp.get('liquidity', 0):.0f}` | Alavancagem: `{comp.get('leverage', 0):.0f}`\n"
            f"  Whale: `{comp.get('whale', 0):.0f}` | Compressão: `{comp.get('compression', 0):.0f}`\n\n"
            f"🔑 *Sinais:*\n{signals_text}"
            f"{target_str}\n\n"
            f"📈 L/S: `{ls_ratio:.2f}x`  |  Funding: `{funding*100:.4f}%`\n"
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
        log.warning(f"[CrashTrader] Telegram error: {e}")


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
