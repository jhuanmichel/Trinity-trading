"""
predictive_pump_trader.py — Orquestrador do Predictive Pump Trader (Cap. 20)

NÃO detecta pumps que já aconteceram.
PREDIZ pumps ANTES de acontecerem.

Pipeline a cada ciclo (30s):
  1. AltcoinMarketScanner.scan_universe()  → top 50 pump candidates (cache 5min)
  2. AltcoinMarketScanner.fetch_batch()    → dados completos em paralelo
  3. Para cada coin:
     a. SilentAccumulationDetector         — funding + OI + CVD (0-25)
     b. ShortSqueezeDetector
     c. LiquidityGravityDetector
     d. BreakoutPressureDetector
     e. PumpScoringEngine → pump_score final (4×25)
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
SCAN_INTERVAL_S    = 60
TOP_RESULTS        = 5         # top N oportunidades institucionais
OPP_THRESHOLD      = 35        # mínimo para aparecer no dashboard
ALERT_THRESHOLD    = 55        # mínimo para disparar alerta Telegram
LAUNCH_THRESHOLD   = 75        # alerta urgente (cooldown reduzido)
BASE_DIR           = Path(__file__).parent.parent.parent.parent
SCAN_OUTPUT_FILE   = BASE_DIR / "dashboard" / "pump_scan_latest.json"

_alert_cooldown:   dict = {}    # {symbol: last_alert_ts}
_alert_last_score: dict = {}    # {symbol: último opportunity_score alertado}
_alert_last_class: dict = {}    # {symbol: último move_classification alertado}
ALERT_COOLDOWN_S    = 3600      # 60min entre alertas do mesmo símbolo
LAUNCH_COOLDOWN_S   = 1800      # 30min para alertas urgentes (score >= LAUNCH_THRESHOLD)


# ── Dataclass de resultado por coin ───────────────────────────────────────────

@dataclass
class PumpCandidate:
    symbol:              str
    price:               float
    price_change_pct:    float
    pump_score:          float
    pump_probability:    str       # LOW/MEDIUM/HIGH/EXTREME
    urgency:             str       # WATCH/ALERT/READY/LAUNCH
    recommended_action:  str
    signal_valid:        bool
    component_scores:    dict      # {silent_acc, squeeze, gravity, breakout} cada 0-25
    top_signals:         list
    funding_rate:        float
    long_short_ratio:    float
    oi_change_pct:       float
    pump_target:         float     # preço alvo estimado (gravidade de liquidez)
    volume_24h:          float
    scanned_at:          str
    # Institutional Volatility Engine fields
    expected_move_pct:   float     # movimento esperado em %
    move_classification: str       # MICRO/WEAK/TRADEABLE/STRONG/EXTREME
    opportunity_score:   float     # score composto 0-100
    # Trinity Signal Engine fields
    entry:               float     # preço de entrada (market long)
    stop:                float     # stop loss abaixo da entrada
    tp1:                 float     # take profit 1 (35% do movimento)
    tp2:                 float     # take profit 2 (65% do movimento)
    tp3:                 float     # take profit 3 (100% do movimento)
    probability_pct:     float     # probabilidade estimada de sucesso %
    dna_pattern:         str       # padrão DNA institucional detectado


# ── Orquestrador ──────────────────────────────────────────────────────────────

class PredictivePumpTrader:
    """Orquestrador do Predictive Pump Trader. Thread-safe via asyncio.to_thread()."""

    def __init__(self):
        from trinity.traders.predictive_pump_trader.altcoin_market_scanner import (
            scan_universe, fetch_batch,
        )
        from trinity.traders.predictive_pump_trader.silent_accumulation_detector import (
            detect_silent_accumulation,
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
        from trinity.traders.predictive_pump_trader.pump_scoring_engine import (
            score_pump,
        )

        self._scan_universe   = scan_universe
        self._fetch_batch     = fetch_batch
        self._detect_silent   = detect_silent_accumulation
        self._detect_squeeze  = detect_short_squeeze
        self._detect_gravity  = detect_liquidity_gravity
        self._detect_breakout = detect_breakout_pressure
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

        # ── BTC Regime context ─────────────────────────────────────────────
        try:
            from trinity.traders.btc_regime_monitor import get_btc_regime
            btc_regime = get_btc_regime()
            btc_dir    = btc_regime.get("direction", "?")
            btc_str    = btc_regime.get("strength", 0)
            btc_trans  = btc_regime.get("transition", "")
            trans_info = f" | TRANSIÇÃO: {btc_trans}" if btc_trans else ""
            log.info(
                f"[PumpTrader] BTC: {btc_dir} (strength={btc_str:.0f}, "
                f"confirmations={btc_regime.get('confirmations', 0)}, "
                f"bias={btc_regime.get('bias', '?')}){trans_info}"
            )
        except Exception:
            pass

        results: List[PumpCandidate] = []
        for coin_data in coin_data_list:
            try:
                candidate = self._analyze_coin(coin_data, scan_ts)
                if candidate:
                    results.append(candidate)
            except Exception as e:
                log.warning(f"[PumpTrader] Erro ao analisar {coin_data.get('symbol', '?')}: {e}")

        results.sort(key=lambda c: c.opportunity_score, reverse=True)
        qualified = [c for c in results if c.opportunity_score >= OPP_THRESHOLD]
        top = qualified[:TOP_RESULTS]

        duration = round(time.time() - t0, 2)
        log.info(
            f"[PumpTrader] Scan concluído em {duration}s — "
            f"{len(qualified)} oportunidades >= {OPP_THRESHOLD} | "
            f"top: {[(c.symbol, round(c.opportunity_score), c.move_classification) for c in top[:3]]}"
        )

        return {
            "scan_ts":         scan_ts,
            "scan_duration_s": duration,
            "coins_scanned":   len(coin_data_list),
            "candidates":      [asdict(c) for c in top],
        }

    def _analyze_coin(self, coin_data: dict, scan_ts: str) -> Optional[PumpCandidate]:
        symbol = coin_data.get("symbol", "?")

        # ── Manipulation check — fail-open ─────────────────────────────────
        try:
            from trinity.traders.manipulation_detector import check_manipulation
            manip = check_manipulation(symbol, coin_data)
            if manip["locked"]:
                log.info(f"[PumpTrader] {symbol} SKIPPED (manipulação): {manip['reason']}")
                return None
        except Exception:
            pass  # fail-open: qualquer erro não bloqueia a análise

        silent_result   = self._detect_silent(coin_data)
        squeeze_result  = self._detect_squeeze(coin_data)
        gravity_result  = self._detect_gravity(coin_data)
        breakout_result = self._detect_breakout(coin_data)

        # Score final — passa coin_data para o Expected Move Model
        score_result = self._score_pump(
            silent_result, squeeze_result, gravity_result,
            breakout_result, coin_data,
        )

        price           = coin_data.get("price", 0)
        pump_score      = score_result["pump_score"]
        expected_move   = score_result.get("expected_move_pct", 0.0)
        opp_score       = score_result.get("opportunity_score", 0.0)
        dna_pattern     = score_result.get("dna_pattern", "")
        probability_pct = _calc_probability(opp_score, dna_pattern)
        entry, stop, tp1, tp2, tp3 = _calc_pump_levels(price, expected_move)

        return PumpCandidate(
            symbol              = symbol,
            price               = price,
            price_change_pct    = coin_data.get("price_change_pct", 0),
            pump_score          = pump_score,
            pump_probability    = score_result["pump_probability"],
            urgency             = score_result["urgency"],
            recommended_action  = score_result["recommended_action"],
            signal_valid        = score_result["signal_valid"],
            component_scores    = score_result["component_scores"],
            top_signals         = score_result["top_signals"],
            funding_rate        = coin_data.get("funding_rate", 0),
            long_short_ratio    = coin_data.get("long_short_ratio", 1.0),
            oi_change_pct       = coin_data.get("oi_change_pct", 0),
            pump_target         = score_result.get("pump_target", 0),
            volume_24h          = coin_data.get("volume_24h", 0),
            scanned_at          = scan_ts,
            expected_move_pct   = expected_move,
            move_classification = score_result.get("move_classification", "MICRO"),
            opportunity_score   = opp_score,
            entry               = entry,
            stop                = stop,
            tp1                 = tp1,
            tp2                 = tp2,
            tp3                 = tp3,
            probability_pct     = probability_pct,
            dna_pattern         = dna_pattern,
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


def run_pump_cycle() -> dict:
    """
    Ciclo completo: scan + salvar resultado + alertas Telegram (sync).
    Usar no scheduler (main.py) e nos loops do servidor (server.py).
    """
    global _instance
    if _instance is None:
        _instance = PredictivePumpTrader()
    result = _instance.run_scan()
    _save_result(result)

    import time as _t
    now     = _t.time()
    alerted = 0
    for c in result.get("candidates", []):
        score  = c.get("opportunity_score", 0)
        symbol = c.get("symbol", "")
        cls    = c.get("move_classification", "")

        if score < ALERT_THRESHOLD:
            break

        last_alert = _alert_cooldown.get(symbol, 0)
        cooldown   = LAUNCH_COOLDOWN_S if score >= LAUNCH_THRESHOLD else ALERT_COOLDOWN_S

        if (now - last_alert) < cooldown:
            last_cls   = _alert_last_class.get(symbol, "")
            last_score = _alert_last_score.get(symbol, 0.0)
            tier_up    = _tier_rank(cls) > _tier_rank(last_cls)
            score_up   = (score - last_score) >= 15.0
            if not tier_up and not score_up:
                continue

        try:
            _send_pump_telegram(c)
            _alert_cooldown[symbol]   = now
            _alert_last_score[symbol] = score
            _alert_last_class[symbol] = cls
            alerted += 1
        except Exception as _e:
            log.warning(f"[PumpTrader] Telegram error ({symbol}): {_e}")

    if alerted:
        log.info(f"[PumpTrader] {alerted} alertas Telegram enviados")
    return result


# ── Trinity Signal Helpers ────────────────────────────────────────────────────

def _calc_pump_levels(price: float, expected_move_pct: float):
    """
    Calcula ENTRY/STOP/TP1/TP2/TP3 para long (pump).

    Stop:  1.5% abaixo (ou 20% do movimento esperado se maior)
    TP1:   35% do movimento esperado
    TP2:   65% do movimento esperado
    TP3:  100% do movimento esperado (alvo total)
    """
    if price <= 0 or expected_move_pct <= 0:
        return price, price, price, price, price
    stop_pct = max(1.5, expected_move_pct * 0.20)
    entry = price
    stop  = price * (1 - stop_pct / 100)
    tp1   = price * (1 + expected_move_pct * 0.35 / 100)
    tp2   = price * (1 + expected_move_pct * 0.65 / 100)
    tp3   = price * (1 + expected_move_pct / 100)
    return entry, stop, tp1, tp2, tp3


def _calc_probability(opportunity_score: float, dna_pattern: str) -> float:
    """
    Estima probabilidade de sucesso do sinal.

    Baseado no opportunity_score e presença de padrão DNA institucional.
    """
    if opportunity_score >= 90:
        prob = 85.0
    elif opportunity_score >= 80:
        prob = 74.0
    elif opportunity_score >= 70:
        prob = 63.0
    else:
        prob = 52.0
    if dna_pattern:
        prob += 7.0
    return min(92.0, prob)


def _fmt_p(p: float) -> str:
    """Formata preço com precisão adequada."""
    if p <= 0:   return "—"
    if p < 1:    return f"${p:,.5f}"
    if p < 10:   return f"${p:,.4f}"
    if p < 100:  return f"${p:,.3f}"
    return f"${p:,.2f}"


def _pct_diff(level: float, ref: float, direction: str = "up") -> str:
    """Retorna string de variação % entre level e ref."""
    if ref <= 0: return ""
    pct = abs(level - ref) / ref * 100
    sign = "+" if direction == "up" else "-"
    return f"({sign}{pct:.1f}%)"


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
        score  = c.get("opportunity_score", 0)    if isinstance(c, dict) else c.opportunity_score
        symbol = c.get("symbol", "")              if isinstance(c, dict) else c.symbol
        cls    = c.get("move_classification", "") if isinstance(c, dict) else c.move_classification

        if score < ALERT_THRESHOLD:
            break

        # Cooldown por símbolo
        last_alert = _alert_cooldown.get(symbol, 0)
        cooldown   = LAUNCH_COOLDOWN_S if score >= LAUNCH_THRESHOLD else ALERT_COOLDOWN_S

        if (now - last_alert) < cooldown:
            # Dentro do cooldown: re-alerta só se tier subiu ou score saltou >= 15pts
            last_cls   = _alert_last_class.get(symbol, "")
            last_score = _alert_last_score.get(symbol, 0.0)
            tier_up    = _tier_rank(cls) > _tier_rank(last_cls)
            score_up   = (score - last_score) >= 15.0
            if not tier_up and not score_up:
                continue

        try:
            await asyncio.to_thread(_send_pump_telegram, c if isinstance(c, dict) else asdict(c))
            _alert_cooldown[symbol]   = now
            _alert_last_score[symbol] = score
            _alert_last_class[symbol] = cls
            alerted += 1
        except Exception as e:
            log.warning(f"[PumpTrader] Telegram falhou para {symbol}: {e}")

    if alerted:
        log.info(f"[PumpTrader] {alerted} alertas Telegram enviados")


def _send_pump_telegram(c: dict):
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
        import requests as _req

        symbol      = c.get("symbol", "?")
        price       = c.get("price", 0)
        pct_change  = c.get("price_change_pct", 0)
        opp_score   = c.get("opportunity_score", 0)
        move_pct    = c.get("expected_move_pct", 0)
        move_cls    = c.get("move_classification", "WEAK")
        action      = c.get("recommended_action", "—")
        comp        = c.get("component_scores", {})
        signals     = c.get("top_signals", [])
        dna_pattern = c.get("dna_pattern", "")
        prob_pct    = c.get("probability_pct", 0)
        entry       = c.get("entry", price)
        stop        = c.get("stop", 0)
        tp1         = c.get("tp1", 0)
        tp2         = c.get("tp2", 0)
        tp3         = c.get("tp3", 0)

        tier_map   = {"EXTREME": "🔥🔥🔥", "STRONG": "🔥🔥", "TRADEABLE": "🔥", "WEAK": "⚡", "MICRO": "👁"}
        tier_emoji = tier_map.get(move_cls, "⚡")
        header     = "🚀" if opp_score >= 75 else "📡"
        pct_str    = f"+{pct_change:.1f}%" if pct_change >= 0 else f"{pct_change:.1f}%"

        def bar(val, mx=25):
            filled = max(0, min(8, int(val / mx * 8)))
            return "█" * filled + "░" * (8 - filled)

        # Sinais — máximo 4 para não estourar 4096 chars
        sig_lines = ""
        for s in signals[:4]:
            sig_lines += f"\n• {s}"

        msg = (
            f"{header} *TRINITY — LONG SETUP*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{tier_emoji} *{move_cls}*"
        )
        if dna_pattern:
            msg += f" — {dna_pattern}"
        msg += (
            f"\n🪙 `{symbol}` | {pct_str} | {_fmt_p(price)}\n\n"
            f"🎯 *Score: {opp_score:.0f}* | Move: *+{move_pct:.1f}%* | Prob: *{prob_pct:.0f}%*\n\n"
            f"📊 *DETECTORES:*\n"
            f"  Silent:  `{bar(comp.get('silent_acc', 0))}` {comp.get('silent_acc', 0):.1f}\n"
            f"  Squeeze: `{bar(comp.get('squeeze', 0))}` {comp.get('squeeze', 0):.1f}\n"
            f"  Gravity: `{bar(comp.get('gravity', 0))}` {comp.get('gravity', 0):.1f}\n"
            f"  Breakout:`{bar(comp.get('breakout', 0))}` {comp.get('breakout', 0):.1f}\n\n"
            f"🔑 *SINAIS:*{sig_lines}\n\n"
            f"📌 *TRADE PLAN:*\n"
            f"  `LONG  {_fmt_p(entry)}`\n"
            f"  `STOP  {_fmt_p(stop)}  {_pct_diff(stop, entry, 'down')}`\n"
            f"  `TP1   {_fmt_p(tp1)}  {_pct_diff(tp1, entry, 'up')}`\n"
            f"  `TP2   {_fmt_p(tp2)}  {_pct_diff(tp2, entry, 'up')}`\n"
            f"  `TP3   {_fmt_p(tp3)}  {_pct_diff(tp3, entry, 'up')}`\n\n"
            f"💡 _{action}_"
        )

        # Truncar se exceder limite Telegram (4096 chars)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n_...truncado_"

        _req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=8,
        )

        # Registrar sinal no OutcomeTracker para tracking de win rate
        try:
            from datetime import datetime, timezone as _tz
            from outcome_tracker import get_tracker
            conviction = "HIGH" if move_cls in ("STRONG", "EXTREME") else "MEDIUM"
            get_tracker().register_signal({
                "direction":       "LONG",
                "score":           opp_score,
                "entry_price":     entry,
                "stop_loss":       stop,
                "tp1":             tp1,
                "tp2":             tp2,
                "symbol":          symbol,
                "timestamp":       datetime.now(_tz.utc).isoformat(),
                "conviction_tier": conviction,
                "layer_scores":    comp,
            })
            log.info(f"[PumpTrader] OutcomeTracker: sinal LONG registrado — {symbol} score={opp_score:.0f}")
        except Exception as _oe:
            log.debug(f"[PumpTrader] OutcomeTracker register_signal falhou: {_oe}")
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


# Ranking de tiers para detectar upgrade (MICRO→WEAK→TRADEABLE→STRONG→EXTREME)
_TIER_RANK = {"MICRO": 0, "WEAK": 1, "TRADEABLE": 2, "STRONG": 3, "EXTREME": 4}

def _tier_rank(cls: str) -> int:
    return _TIER_RANK.get(cls, 0)
