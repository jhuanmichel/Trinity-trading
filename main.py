"""
main.py — ORQUESTRADOR PRINCIPAL
Roda todos os módulos na sequência correta e envia alertas.
Execute com: python main.py
"""
import schedule
import time
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# Módulos do agente
from mexc_client    import get_ohlcv, get_current_price, get_orderbook
from indicators     import regime, trend, momentum, volume, derivatives, onchain, sentiment, liquidations
from indicators     import market_structure, correlation
from scoring        import calculate_score
from institutional_scoring import calculate_institutional_score
from smart_money_engine import SmartMoneyEngine
from market_maker_engine import run_market_maker_analysis
from btc_liquidation_engine import get_dashboard_section as _liq_section, start_background as _liq_start, get_for_scoring as _liq_scoring
from pressure_meter import calculate_pressure
from rare_setup_detector import detect_rare_setup
from geopolitical_engine import run_geo_analysis
from bitcoin_cycle_engine import run_bitcoin_cycle_analysis
from institutional_direction_engine import run_institutional_direction_analysis
from neural_engine import run_neural_analysis
import agent
import alerts
from config         import (
    SYMBOL, TIMEFRAME, SCORE_THRESHOLD,
    SUMMARY_INTERVAL_MINUTES, SIGNAL_INTERVAL_MINUTES,
    INST_INTERVAL_MINUTES, INST_SCORE_THRESHOLD,
)
from morning_brief       import run_morning_brief
from cycle_intelligence import run_cycle_intelligence
from funding_rate_manager import get_manager as _get_funding_manager
from trinity.traders.predictive_pump_trader.predictive_pump_trader import run_pump_cycle as _run_pump_cycle
from trinity.traders.predictive_crash_trader.predictive_crash_trader import run_crash_cycle as _run_crash_cycle
from outcome_tracker import get_tracker as _get_tracker
from high_conviction_filter import get_filter as _get_hcf


# ─── Logging ───────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(f"logs/agent_{datetime.now().strftime('%Y%m')}.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

# ─── Rate-limit para alerta Anthropic (1x/hora) ───────────────────────────────
_last_anthropic_alert: float = 0.0


def _alert_anthropic_failure(error_msg: str) -> None:
    """Envia alerta Telegram quando API Anthropic falha. Máx 1x por hora."""
    global _last_anthropic_alert
    if time.time() - _last_anthropic_alert < 3600:
        return
    try:
        alerts.send_message(
            "⚠️ <b>TRINITY — Anthropic API falhou</b>\n"
            "A análise de IA está desativada neste ciclo.\n"
            f"Verificar <code>ANTHROPIC_API_KEY</code> no Render.\n\n"
            f"Erro: <code>{str(error_msg)[:120]}</code>"
        )
        _last_anthropic_alert = time.time()
        log.warning("[ANTHROPIC] Alerta Telegram enviado (próximo em 1h)")
    except Exception as e:
        log.warning(f"[ANTHROPIC] Falha ao enviar alerta Telegram: {e}")


def run_analysis_summary():
    """
    Tarefa 1: Análise completa dos módulos, SEM Claude.
    Envia apenas resumo simples (preço, score, sinal) via send_summary.
    """
    log.info("=" * 60)
    log.info(f"📋 [RESUMO] Iniciando análise | {SYMBOL} {TIMEFRAME}")
    log.info("=" * 60)

    try:
        log.info("📊 [1/9] Buscando dados MEXC...")
        df    = get_ohlcv(SYMBOL, TIMEFRAME, limit=300)
        price = get_current_price(SYMBOL)
        ob    = get_orderbook(SYMBOL)
        log.info(f"   Preço atual: ${price:,.2f} | Order book: imbalance {ob['imbalance_pct']:+.1f}%")

        log.info("🔍 [2/9] Regime...")
        regime_data = regime.analyze(df)
        log.info("📈 [3/9] Tendência...")
        trend_data = trend.analyze(df)
        log.info("⚡ [4/9] Momentum...")
        momentum_data = momentum.analyze(df)
        log.info("📦 [5/9] Volume...")
        volume_data = volume.analyze(df)
        log.info("📉 [6/9] Derivativos...")
        derivatives_data = derivatives.analyze("BTC")
        log.info("💥 [6b] Liquidações...")
        liquidations_data = liquidations.analyze("BTC")
        log.info("🔗 [7/9] On-chain...")
        onchain_data = onchain.analyze("BTC")
        log.info("😱 [8/9] Sentimento...")
        sentiment_data = sentiment.analyze()

        analyses = {
            "regime": regime_data, "trend": trend_data, "momentum": momentum_data,
            "volume": volume_data, "derivatives": derivatives_data,
            "liquidations": liquidations_data, "onchain": onchain_data,
            "sentiment": sentiment_data,
        }
        score_data = calculate_score(analyses)
        log.info(f"🧮 [9/9] Score: {score_data.get('final_score', 50):.1f}/100 — {score_data.get('signal', 'NO_TRADE')}")

        # alerts.send_summary(price, score_data)  # DESATIVADO: redundante com BTC Regime Engine
        log.info("✅ Resumo calculado (Telegram desativado).\n")

    except Exception as e:
        log.error(f"💥 ERRO (resumo): {e}", exc_info=True)
        alerts.send_error(str(e))


def run_analysis_signal():
    """
    Tarefa 2: Análise completa INCLUINDO Claude.
    Envia sinal completo (entrada, stop, alvos) via send_signal quando score ≥ threshold.
    """
    log.info("=" * 60)
    log.info(f"🔔 [SINAL] Iniciando análise + IA | {SYMBOL} {TIMEFRAME}")
    log.info("=" * 60)

    try:
        log.info("📊 [1/9] Buscando dados MEXC...")
        df    = get_ohlcv(SYMBOL, TIMEFRAME, limit=300)
        price = get_current_price(SYMBOL)
        ob    = get_orderbook(SYMBOL)
        log.info(f"   Preço atual: ${price:,.2f} | Order book: imbalance {ob['imbalance_pct']:+.1f}%")

        log.info("🔍 [2/9] Regime...")
        regime_data = regime.analyze(df)
        log.info("📈 [3/9] Tendência...")
        trend_data = trend.analyze(df)
        log.info("⚡ [4/9] Momentum...")
        momentum_data = momentum.analyze(df)
        log.info("📦 [5/9] Volume...")
        volume_data = volume.analyze(df)
        log.info("📉 [6/9] Derivativos...")
        derivatives_data = derivatives.analyze("BTC")
        log.info("💥 [6b] Liquidações...")
        liquidations_data = liquidations.analyze("BTC")
        log.info("🔗 [7/9] On-chain...")
        onchain_data = onchain.analyze("BTC")
        log.info("😱 [8/9] Sentimento...")
        sentiment_data = sentiment.analyze()

        analyses = {
            "regime": regime_data, "trend": trend_data, "momentum": momentum_data,
            "volume": volume_data, "derivatives": derivatives_data,
            "liquidations": liquidations_data, "onchain": onchain_data,
            "sentiment": sentiment_data,
        }
        score_data = calculate_score(analyses)
        log.info(f"\n{score_data['summary']}")

        log.info("🧠 Consultando agente de IA (Claude)...")
        ai_result = agent.analyze(price, analyses, score_data)

        if "error" in ai_result:
            log.error(f"   Erro na IA: {ai_result['error']}")
            _alert_anthropic_failure(ai_result["error"])
        else:
            log.info(f"   IA → {ai_result['direcao']} | Força: {ai_result['forca_sinal']}/10")

        final_score = score_data.get("final_score", 50)

        if final_score >= SCORE_THRESHOLD or final_score <= (100 - SCORE_THRESHOLD):
            log.info(f"🔔 Score {final_score} ≥ threshold {SCORE_THRESHOLD} → enviando sinal completo!")
            alerts.send_signal(price, score_data, ai_result, analyses)
        else:
            log.info(f"💤 Score {final_score} abaixo do threshold ({SCORE_THRESHOLD}) — sem sinal completo")

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "type":      "signal",
            "price":     price,
            "score":     score_data,
            "ai":        ai_result,
        }
        with open(f"logs/signals_{datetime.now().strftime('%Y%m%d')}.jsonl", "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        log.info("✅ Análise com IA concluída.\n")

    except Exception as e:
        log.error(f"💥 ERRO CRÍTICO (sinal): {e}", exc_info=True)
        alerts.send_error(str(e))


def _write_dashboard_state(
    price, inst, ms_data, volume_data, trend_data,
    corr_data, regime_data, entry, stop, tp1, tp2, tp3,
    smc_signal=None, mm_data=None,
    pressure_data=None, rare_data=None, trinity_score=None,
    geo_data=None, cycle_data=None, direction_data=None,
    neural_data=None,
):
    """Persiste o estado atual para o dashboard web (dashboard/current_state.json)."""
    state = {
        "last_updated": datetime.now().isoformat(),
        "btc": {
            "symbol":           SYMBOL,
            "price":            price,
            "direction":        inst.get("direction", "AGUARDANDO"),
            "inst_score":       inst.get("inst_score", 50),
            "strength":         inst.get("strength", "NEUTRO"),
            "confluences":      inst.get("confluences", 0),
            "valid":            inst.get("valid", False),
            "entry": entry, "stop": stop,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "breakdown":        inst.get("breakdown", {}),
            "layer_scores":     inst.get("layer_scores", {}),
            # Market Structure
            "market_structure": ms_data.get("structure", "?"),
            "bos_bull":         ms_data.get("bos_bull", False),
            "bos_bear":         ms_data.get("bos_bear", False),
            "choch":            ms_data.get("choch", False),
            "sweep_low":        ms_data.get("sweep_low", False),
            "sweep_high":       ms_data.get("sweep_high", False),
            "equal_highs":      ms_data.get("equal_highs", False),
            "equal_lows":       ms_data.get("equal_lows", False),
            # Volume
            "high_volume":      volume_data.get("high_volume", False),
            "cvd_trending_up":  volume_data.get("cvd_trending_up", False),
            "vol_ratio":        volume_data.get("vol_ratio", 1.0),
            "healthy_move":     volume_data.get("healthy_move", False),
            "trap_signal":      volume_data.get("trap_signal", False),
            # Trend
            "ema_signal":            trend_data.get("ema_signal", "?"),
            "ichimoku_above_cloud":  trend_data.get("ichimoku_above_cloud", False),
            "ichimoku_below_cloud":  trend_data.get("ichimoku_below_cloud", False),
            # Correlation
            "correlation_bias": corr_data.get("bias", "NEUTRO"),
            "btc_change":       corr_data.get("btc_change", 0),
            "eth_change":       corr_data.get("eth_change", 0),
            "btc_dominance":    corr_data.get("btc_dominance", 50),
            "usdt_dominance":   corr_data.get("usdt_dominance", 10),
            # Volatility
            "regime":   regime_data.get("regime", "?"),
            "atr_pct":  regime_data.get("atr_pct", 0),
            "squeeze":  regime_data.get("squeeze", False),
            "adx":      regime_data.get("adx", 0),
            # Smart Money Concepts
            "smart_money": {
                "smc_score":  smc_signal.get("smc_score")    if smc_signal else None,
                "bias":       smc_signal.get("bias")          if smc_signal else None,
                "direction":  smc_signal.get("direction")     if smc_signal else None,
                "alignment":  smc_signal.get("alignment")     if smc_signal else None,
                "confidence": smc_signal.get("confidence")    if smc_signal else None,
                "valid":      smc_signal.get("valid", False)  if smc_signal else False,
                "entry":      smc_signal.get("entry")         if smc_signal else None,
                "stop":       smc_signal.get("stop")          if smc_signal else None,
                "targets":    smc_signal.get("targets", {})   if smc_signal else {},
                "structure":  smc_signal.get("structure", {}) if smc_signal else {},
                "reasoning":  smc_signal.get("reasoning")     if smc_signal else None,
            } if smc_signal else None,
            # Liquidações em tempo real (Binance WebSocket)
            "liquidations_live": _liq_section(),
            # Market Maker Engine
            "market_maker": {
                "bias":                  mm_data.get("bias"),
                "confidence":            mm_data.get("confidence"),
                "market_maker_score":    mm_data.get("market_maker_score"),
                "trap_probability":      mm_data.get("trap_probability"),
                "trap_direction":        mm_data.get("trap_direction"),
                "trap_signals":          mm_data.get("trap_signals", []),
                "sweep_strength":        mm_data.get("sweep_strength"),
                "sweep_bias":            mm_data.get("sweep_bias"),
                "liquidity_target_high": mm_data.get("liquidity_target_high"),
                "liquidity_target_low":  mm_data.get("liquidity_target_low"),
                "premium_zone":          mm_data.get("premium_zone"),
                "discount_zone":         mm_data.get("discount_zone"),
                "equilibrium_zone":      mm_data.get("equilibrium_zone"),
                "range_position_pct":    mm_data.get("range_position_pct"),
                "equilibrium_price":     mm_data.get("equilibrium_price"),
                "fib_382":               mm_data.get("fib_382"),
                "fib_618":               mm_data.get("fib_618"),
                "institutional_bias":    mm_data.get("institutional_bias"),
                "valid":                 mm_data.get("valid", False),
                "confluences":           mm_data.get("confluences", 0),
                "equal_highs":           mm_data.get("equal_highs", []),
                "equal_lows":            mm_data.get("equal_lows", []),
                "stop_hunt_zones":       mm_data.get("stop_hunt_zones", []),
            } if mm_data else None,
            # Medidor de Pressão Institucional (IPM — Cap. 4)
            "pressure_meter": {
                "pressure":      pressure_data.get("pressure", 0),
                "direction":     pressure_data.get("direction", "NEUTRAL"),
                "filter_passed": pressure_data.get("filter_passed", False),
                "components":    pressure_data.get("components", {}),
            } if pressure_data else None,
            # Detector de Setups Raros (Cap. 3)
            "rare_setup": {
                "active":         rare_data.get("rare_setup", False),
                "score":          rare_data.get("score", 0),
                "setup_type":     rare_data.get("setup_type", "NENHUM"),
                "factors_active": rare_data.get("factors_active", []),
                "components":     rare_data.get("components", {}),
                "signal_bonus":   rare_data.get("signal_bonus", 0),
            } if rare_data else None,
            # Trinity Score final (Cap. 7 + v2 com Geo)
            "trinity_score": trinity_score,
            # Geopolitical Intelligence Engine (Cap. 14)
            "geopolitical": {
                "geo_score":         geo_data.get("geo_score",         50.0),
                "geo_bias":          geo_data.get("geo_bias",          "NEUTRAL"),
                "geo_confidence":    geo_data.get("geo_confidence",    0.0),
                "risk_sentiment":    geo_data.get("risk_sentiment",    "NEUTRAL"),
                "liquidity_outlook": geo_data.get("liquidity_outlook", "NEUTRAL"),
                "top_event":         geo_data.get("top_event",         "N/A"),
                "top_event_source":  geo_data.get("top_event_source",  "N/A"),
                "top_event_category":geo_data.get("top_event_category","MARKET"),
                "rare_macro_setup":  geo_data.get("rare_macro_setup",  False),
                "rare_macro_combo":  geo_data.get("rare_macro_combo",  None),
                "high_impact_count": geo_data.get("high_impact_count", 0),
                "article_count":     geo_data.get("article_count",     0),
                "top_articles":      geo_data.get("top_articles",      []),
            } if geo_data else None,
            # Bitcoin Cycle Engine (Cap. 15)
            "bitcoin_cycle": {
                "cycle_phase":        cycle_data.get("cycle_phase",        "ACCUMULATION"),
                "cycle_confidence":   cycle_data.get("cycle_confidence",   0.0),
                "cycle_strength":     cycle_data.get("cycle_strength",     "WEAK"),
                "cycle_score":        cycle_data.get("cycle_score",        50.0),
                "risk_level":         cycle_data.get("risk_level",         "MEDIUM"),
                "risk_multiplier":    cycle_data.get("risk_multiplier",    1.0),
                "bias_adjustment":    cycle_data.get("bias_adjustment",    "NEUTRAL"),
                "macro_trend":        cycle_data.get("macro_trend",        "NEUTRAL"),
                "cycle_position_pct": cycle_data.get("cycle_position_pct", 50.0),
                "expected_volatility":cycle_data.get("expected_volatility","MEDIUM"),
                "rare_cycle_setup":   cycle_data.get("rare_cycle_setup",   False),
                "rare_cycle_type":    cycle_data.get("rare_cycle_type",    None),
                "phase_description":  cycle_data.get("phase_description",  ""),
                "active_signals":     cycle_data.get("active_signals",     []),
                "halving":            cycle_data.get("halving",            {}),
                "trend":              cycle_data.get("trend",              {}),
                "onchain":            cycle_data.get("onchain",            {}),
            } if cycle_data else None,
            # Institutional Direction Engine (Cap. 16)
            "institutional_direction": {
                "direction":              direction_data.get("direction",              "NEUTRAL"),
                "direction_confidence":   direction_data.get("direction_confidence",   50.0),
                "direction_score":        direction_data.get("direction_score",        50.0),
                "breakout_probability":   direction_data.get("breakout_probability",   30.0),
                "fake_move_probability":  direction_data.get("fake_move_probability",  30.0),
                "dominant_market":        direction_data.get("dominant_market",        "BALANCED"),
                "move_validity":          direction_data.get("move_validity",          "WEAK"),
                "market_maker_defense":   direction_data.get("market_maker_defense",   False),
                "defense_level":          direction_data.get("defense_level",          None),
                "defense_type":           direction_data.get("defense_type",           None),
                "defense_strength":       direction_data.get("defense_strength",       0.0),
                "mm_score":               direction_data.get("mm_score",               50.0),
                "delta_score":            direction_data.get("delta_score",            50.0),
                "perp_spot_score":        direction_data.get("perp_spot_score",        50.0),
                "institutional_bias":     direction_data.get("institutional_bias",     "NEUTRAL"),
                "absorption_type":        direction_data.get("absorption_type",        None),
                "funding_bias":           direction_data.get("funding_bias",           "NEUTRAL"),
                "funding_extreme":        direction_data.get("funding_extreme",        False),
                "funding_annual_pct":     direction_data.get("funding_annual_pct",     0.0),
                "oi_change_pct":          direction_data.get("oi_change_pct",          0.0),
                "rare_direction_setup":   direction_data.get("rare_direction_setup",   False),
                "rare_direction_type":    direction_data.get("rare_direction_type",    None),
                "rare_direction_strength":direction_data.get("rare_direction_strength",0.0),
                "signals":                direction_data.get("signals",                []),
            } if direction_data else None,
            # Neural Intelligence Engine (Cap. 17)
            "neural_intelligence": {
                "bull_probability":                 neural_data.get("bull_probability",                 20.0),
                "bear_probability":                 neural_data.get("bear_probability",                 20.0),
                "sideways_probability":             neural_data.get("sideways_probability",             35.0),
                "volatility_expansion_probability": neural_data.get("volatility_expansion_probability", 15.0),
                "manipulation_probability":         neural_data.get("manipulation_probability",         10.0),
                "confidence_score":                 neural_data.get("confidence_score",                 20.0),
                "market_regime":                    neural_data.get("market_regime",                    "RANGING"),
                "neural_score":                     neural_data.get("neural_score",                     50.0),
                "neural_bias":                      neural_data.get("neural_bias",                      "NEUTRAL"),
                "bias_strength":                    neural_data.get("bias_strength",                    0.0),
                "bias_confidence":                  neural_data.get("bias_confidence",                  20.0),
                "bull_edge":                        neural_data.get("bull_edge",                        0.0),
                "is_balanced":                      neural_data.get("is_balanced",                      True),
                "rare_neural_setup":                neural_data.get("rare_neural_setup",                False),
                "rare_neural_type":                 neural_data.get("rare_neural_type",                 None),
                "rare_neural_strength":             neural_data.get("rare_neural_strength",             0.0),
                "agreement_score":                  neural_data.get("agreement_score",                  50.0),
                "dominant_model":                   neural_data.get("dominant_model",                   "unknown"),
                "model_confidences":                neural_data.get("model_confidences",                {}),
                "calibration_note":                 neural_data.get("calibration_note",                 ""),
                "retrain_accuracy":                 neural_data.get("retrain_accuracy",                 0.0),
            } if neural_data else None,
        },
    }
    import numpy as np

    class _Enc(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, (np.bool_,)):   return bool(o)
            if isinstance(o, (np.integer,)):  return int(o)
            if isinstance(o, (np.floating,)): return float(o)
            return super().default(o)

    state_path = Path("dashboard/current_state.json")
    state_path.parent.mkdir(exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False, cls=_Enc))


def _calc_levels(price: float, direction: str, atr: float) -> tuple:
    """Calcula entrada, stop e alvos baseados em ATR."""
    atr = atr or price * 0.005   # fallback 0.5%
    if direction == "LONG":
        entry = round(price, 2)
        stop  = round(price - atr * 1.5, 2)
        tp1   = round(price + atr * 1.5, 2)
        tp2   = round(price + atr * 2.5, 2)
        tp3   = round(price + atr * 4.0, 2)
    else:
        entry = round(price, 2)
        stop  = round(price + atr * 1.5, 2)
        tp1   = round(price - atr * 1.5, 2)
        tp2   = round(price - atr * 2.5, 2)
        tp3   = round(price - atr * 4.0, 2)
    return entry, stop, tp1, tp2, tp3


def _run_mtf_market_structure() -> dict:
    """
    Roda market_structure em 4 timeframes (1m, 5m, 15m, 1h).
    Retorna quais TFs confirmam a direção majoritária.
    """
    timeframes = ["1m", "5m", "15m", "1h"]
    results = {}
    for tf in timeframes:
        try:
            df_tf = get_ohlcv(SYMBOL, tf, limit=150)
            ms    = market_structure.analyze(df_tf)
            results[tf] = ms
        except Exception as e:
            log.warning(f"   MTF {tf} falhou: {e}")

    if not results:
        return {"agreed_timeframes": [], "total_timeframes": len(timeframes), "bias": "INDEFINIDO"}

    # Conta quantos TFs concordam com a direção da maioria
    biases = [r.get("bias", "NEUTRO") for r in results.values()]
    long_count  = biases.count("BULLISH")
    short_count = biases.count("BEARISH")

    if long_count >= short_count:
        dominant = "BULLISH"
        agreed   = [tf for tf, r in results.items() if r.get("bias") == "BULLISH"]
    else:
        dominant = "BEARISH"
        agreed   = [tf for tf, r in results.items() if r.get("bias") == "BEARISH"]

    return {
        "agreed_timeframes": agreed,
        "total_timeframes":  len(timeframes),
        "bias":              dominant,
        "details":           {tf: r.get("structure", "?") for tf, r in results.items()},
    }


def run_institutional_analysis():
    """
    Análise institucional multi-camadas com MTF.
    Envia sinal somente quando:
      - inst_score['valid'] == True (≥3 confluências)
      - inst_score >= INST_SCORE_THRESHOLD
      - Estrutura de mercado definida (não LATERAL)
    """
    log.info("=" * 60)
    log.info(f"🏛️  [INSTITUCIONAL] Análise multi-camadas | {SYMBOL}")
    log.info("=" * 60)

    try:
        # ── Dados base ────────────────────────────────────────────────────
        log.info("📊 [1] Buscando dados MEXC...")
        df    = get_ohlcv(SYMBOL, TIMEFRAME, limit=300)
        price = get_current_price(SYMBOL)

        # ── Camada 1: Market Structure ─────────────────────────────────────
        log.info("🏗️  [2] Market Structure...")
        ms_data = market_structure.analyze(df)
        log.info(f"   {ms_data['summary']}")

        # ── Camadas existentes ─────────────────────────────────────────────
        log.info("📈 [3] Trend (EMA/MACD/Ichimoku)...")
        trend_data = trend.analyze(df)

        log.info("📦 [4] Volume + CVD...")
        volume_data = volume.analyze(df)

        log.info("📉 [5] Derivativos + Liquidações...")
        deriv_data = derivatives.analyze("BTC")
        liq_data   = liquidations.analyze("BTC")

        log.info("🌍 [6] Correlação Macro...")
        corr_data = correlation.analyze()
        log.info(f"   {corr_data['summary']}")

        log.info("🌋 [7] Volatilidade (Regime)...")
        regime_data = regime.analyze(df)

        # ── Camada 7: Score Institucional ──────────────────────────────────
        log.info("🧮 [8] Calculando score institucional...")
        inst = calculate_institutional_score(
            market_structure_data = ms_data,
            derivatives_data      = deriv_data,
            liquidations_data     = liq_data,
            volume_data           = volume_data,
            trend_data            = trend_data,
            correlation_data      = corr_data,
            regime_data           = regime_data,
        )
        log.info(f"\n{inst['summary']}")

        # ── MTF (multi-timeframe) ──────────────────────────────────────────
        log.info("🕐 [9] Multi-Timeframe Analysis...")
        mtf = _run_mtf_market_structure()
        log.info(f"   MTF: {mtf['agreed_timeframes']} confirmam {mtf['bias']}")

        # ── SMC Engine (Order Blocks, FVG, MTF SMC) ────────────────────────
        log.info("🧿 [10] Smart Money Concepts (OB + FVG + MTF)...")
        try:
            engine     = SmartMoneyEngine(df, SYMBOL)
            smc_signal = engine.analyze()
            log.info(f"   SMC: {smc_signal['direction']} | score={smc_signal['smc_score']} | {smc_signal['alignment']} | {smc_signal['confidence']}")
        except Exception as e:
            log.warning(f"   SMC Engine falhou: {e}")
            smc_signal = None

        # ── Market Maker Engine ────────────────────────────────────────────
        log.info("🏦 [11] Market Maker Engine (liquidez, sweep, trap, premium/discount)...")
        try:
            mm_data = run_market_maker_analysis(
                symbol           = SYMBOL,
                df               = df,
                trend_score      = trend_data.get("score", 50),
                correlation_score= corr_data.get("score", 50),
            )
            log.info(
                f"   MM: bias={mm_data['bias']} | score={mm_data['market_maker_score']} "
                f"| trap={mm_data['trap_probability']}% | sweep={mm_data['sweep_strength']} "
                f"| {'PREMIUM' if mm_data['premium_zone'] else 'DISCOUNT' if mm_data['discount_zone'] else 'EQUILIBRIUM'}"
            )
        except Exception as e:
            log.warning(f"   Market Maker Engine falhou: {e}")
            mm_data = None

        # ── Medidor de Pressão Institucional (IPM — Cap. 4) ────────────────
        log.info("📡 [12] Medidor de Pressão Institucional (IPM)...")
        try:
            pressure_data = calculate_pressure(
                liq_scoring    = _liq_scoring(),
                volume_data    = volume_data,
                inst_breakdown = inst.get("breakdown", {}),
                mm_data        = mm_data,
                trend_data     = trend_data,
                deriv_data     = deriv_data,
                price          = price,
            )
            direction_ipm = pressure_data["direction"]
            pressure_val  = pressure_data["pressure"]
            ipm_ok        = pressure_data["filter_passed"]
            log.info(f"   IPM: {direction_ipm} | pressão={pressure_val:+.1f} | filtro={'✅' if ipm_ok else '❌'}")
        except Exception as e:
            log.warning(f"   Pressure Meter falhou: {e}")
            pressure_data = {"pressure": 0.0, "direction": "NEUTRAL", "filter_passed": False, "components": {}}
            ipm_ok = False

        # ── Detector de Setups Raros (Cap. 3) ─────────────────────────────
        log.info("⭐ [13] Detector de Setups Raros...")
        try:
            direction_hint = inst.get("direction", "AGUARDANDO")
            rare_data = detect_rare_setup(
                smc_signal  = smc_signal,
                mm_data     = mm_data,
                liq_scoring = _liq_scoring(),
                volume_data = volume_data,
                trend_data  = trend_data,
                price       = price,
                direction   = direction_hint,
            )
            if rare_data["rare_setup"]:
                log.info(f"   ⭐ SETUP RARO: {rare_data['setup_type']} | score={rare_data['score']} "
                         f"| fatores={rare_data['factors_active']}")
            else:
                log.info(f"   Setup comum: score={rare_data['score']} | fatores ativos={len(rare_data['factors_active'])}")
        except Exception as e:
            log.warning(f"   Rare Setup Detector falhou: {e}")
            rare_data = {"rare_setup": False, "score": 0.0, "signal_bonus": 0.0,
                         "setup_type": "NENHUM", "components": {}, "factors_active": []}

        # ── Geopolitical Intelligence Engine (Step 14) ────────────────────
        log.info("🌍 [14] Geopolitical Intelligence Engine...")
        try:
            geo_data = run_geo_analysis()
            log.info(
                f"   Geo: bias={geo_data['geo_bias']} | score={geo_data['geo_score']:.1f} | "
                f"risk={geo_data['risk_sentiment']} | liq={geo_data['liquidity_outlook']} | "
                f"rare_macro={geo_data['rare_macro_setup']} | "
                f"top='{geo_data['top_event'][:55]}'"
            )
        except Exception as e:
            log.warning(f"   Geo Engine falhou: {e}")
            geo_data = {
                "geo_score": 50.0, "geo_bias": "NEUTRAL", "geo_confidence": 0.0,
                "risk_sentiment": "NEUTRAL", "liquidity_outlook": "NEUTRAL",
                "top_event": "N/A", "top_event_source": "N/A", "top_event_category": "MARKET",
                "rare_macro_setup": False, "rare_macro_combo": None,
                "high_impact_count": 0, "article_count": 0, "top_articles": [],
            }

        # ── Bitcoin Cycle Engine (Step 15) ────────────────────────────────
        log.info("🧠 [15] Bitcoin Cycle Engine...")
        try:
            cycle_data = run_bitcoin_cycle_analysis()
            log.info(
                f"   Cycle: phase={cycle_data['cycle_phase']} | "
                f"score={cycle_data['cycle_score']:.1f} | "
                f"bias={cycle_data['bias_adjustment']} | "
                f"risk_mult={cycle_data['risk_multiplier']}x | "
                f"rare={cycle_data['rare_cycle_setup']}"
            )
        except Exception as e:
            log.warning(f"   Cycle Engine falhou: {e}")
            cycle_data = {
                "cycle_phase": "ACCUMULATION", "cycle_confidence": 0.0,
                "cycle_strength": "WEAK",      "cycle_score": 50.0,
                "risk_level": "MEDIUM",         "risk_multiplier": 1.0,
                "bias_adjustment": "NEUTRAL",   "macro_trend": "NEUTRAL",
                "macro_score": 50.0,            "cycle_position_pct": 50.0,
                "expected_volatility": "MEDIUM","rare_cycle_setup": False,
                "rare_cycle_type": None,         "rare_cycle_desc": None,
                "phase_description": f"erro: {e}", "active_signals": [],
                "halving": {}, "trend": {}, "onchain": {}, "volatility": {},
            }

        # ── Institutional Direction Engine (Step 16) ──────────────────────
        log.info("📊 [16] Institutional Direction Engine...")
        try:
            direction_data = run_institutional_direction_analysis(SYMBOL)
            log.info(
                f"   Dir: {direction_data['direction']} | "
                f"conf={direction_data['direction_confidence']:.0f}% | "
                f"score={direction_data['direction_score']:.1f} | "
                f"validity={direction_data['move_validity']} | "
                f"fake={direction_data['fake_move_probability']:.0f}% | "
                f"rare={direction_data['rare_direction_setup']}"
            )
        except Exception as e:
            log.warning(f"   Direction Engine falhou: {e}")
            direction_data = {
                "direction": "NEUTRAL", "direction_confidence": 50.0,
                "direction_score": 50.0, "breakout_probability": 30.0,
                "fake_move_probability": 30.0, "dominant_market": "BALANCED",
                "move_validity": "WEAK", "market_maker_defense": False,
                "defense_level": None, "defense_type": None, "defense_strength": 0.0,
                "mm_score": 50.0, "delta_score": 50.0, "perp_spot_score": 50.0,
                "institutional_bias": "NEUTRAL", "absorption_type": None,
                "funding_bias": "NEUTRAL", "funding_extreme": False,
                "funding_annual_pct": 0.0, "oi_change_pct": 0.0,
                "rare_direction_setup": False, "rare_direction_type": None,
                "rare_direction_strength": 0.0, "signals": [],
            }

        # ── Neural Intelligence Engine (Step 17) ──────────────────────────
        log.info("🧠 [17] Neural Intelligence Engine v2...")
        try:
            # Monta smc_mtf_data se disponível no smc_signal
            smc_mtf_data = smc_signal.get("mtf_analysis") if smc_signal else None
            neural_data = run_neural_analysis(
                symbol         = SYMBOL,
                df             = df,
                price          = price,
                inst_data      = inst,
                smc_data       = smc_signal,
                mm_data        = mm_data,
                pressure_data  = pressure_data,
                rare_data      = rare_data,
                geo_data       = geo_data,
                cycle_data     = cycle_data,
                direction_data = direction_data,
                regime_data    = regime_data,
                volume_data    = volume_data,
                trend_data     = trend_data,
                deriv_data     = deriv_data,
                smc_mtf_data   = smc_mtf_data,
            )
            log.info(
                f"   Neural: bias={neural_data['neural_bias']} | "
                f"bull={neural_data['bull_probability']:.0f}% | "
                f"bear={neural_data['bear_probability']:.0f}% | "
                f"side={neural_data['sideways_probability']:.0f}% | "
                f"regime={neural_data['market_regime']} | "
                f"score={neural_data['neural_score']:.0f} | "
                f"conf={neural_data['confidence_score']:.0f}% | "
                f"rare={neural_data['rare_neural_setup']}"
                + (f" [{neural_data['rare_neural_type']}]" if neural_data.get("rare_neural_type") else "")
            )
        except Exception as e:
            log.warning(f"   Neural Engine falhou: {e}")
            neural_data = {
                "bull_probability": 20.0, "bear_probability": 20.0,
                "sideways_probability": 35.0, "volatility_expansion_probability": 15.0,
                "manipulation_probability": 10.0, "confidence_score": 20.0,
                "market_regime": "RANGING", "neural_score": 50.0,
                "neural_bias": "NEUTRAL", "bias_strength": 0.0, "bias_confidence": 20.0,
                "bull_edge": 0.0, "is_balanced": True, "calibration_note": f"erro: {e}",
                "rare_neural_setup": False, "rare_neural_type": None, "rare_neural_strength": 0.0,
                "agreement_score": 50.0, "dominant_model": "none", "model_confidences": {},
                "retrain_accuracy": 0.0,
            }

        # ── Score final Trinity v6 (Cap. 7 + Geo + Cycle + Direction + Neural) ─
        # formula v6: Confluence×0.22 + |Pressure|×0.13 + RareSetup×0.10 +
        #             Geo×0.10 + Cycle×0.10 + Direction×0.15 + Neural×0.20
        score           = inst["inst_score"]
        valid           = inst["valid"]
        struct          = ms_data.get("structure", "INDEFINIDA")
        lateral         = struct in ("LATERAL", "INDEFINIDA", "TRANSIÇÃO")
        rare_bonus      = rare_data["signal_bonus"]    # 0-100
        pressure_abs    = abs(pressure_data["pressure"])
        geo_score_val   = geo_data.get("geo_score",    50.0)
        cycle_score_val = cycle_data.get("cycle_score", 50.0)
        direction_val   = float(direction_data.get("direction_score", 50.0))
        neural_val      = float(neural_data.get("neural_score", 50.0))

        # Remap pressure e rare para escala [50, 100] com neutro = 50
        # Isso garante baseline Trinity ~50 com mercado neutro (sem pressão / sem raro)
        # Fórmula: 0 → 50 (neutro), 100 → 100 (máximo) via: 50 + valor * 0.50
        pressure_norm = 50.0 + min(pressure_abs, 100.0) * 0.50
        rare_norm     = 50.0 + min(float(rare_bonus), 100.0) * 0.50

        trinity_score = round(
            score * 0.22 + pressure_norm * 0.13 + rare_norm * 0.10
            + geo_score_val * 0.10 + cycle_score_val * 0.10
            + direction_val * 0.15 + neural_val * 0.20,
            1
        )
        log.info(
            f"   📊 Trinity Score v6: {trinity_score:.1f} = "
            f"conf({score:.0f})*0.22 + pressão({pressure_abs:.0f}→{pressure_norm:.0f})*0.13 + "
            f"raro({rare_bonus:.0f}→{rare_norm:.0f})*0.10 + geo({geo_score_val:.1f})*0.10 + "
            f"cycle({cycle_score_val:.1f})*0.10 + dir({direction_val:.1f})*0.15 + "
            f"neural({neural_val:.1f})*0.20"
        )

        # ── Seasonality Engine — aplica multiplicador antes dos thresholds ──
        _seasonal_result_btc = None
        trinity_score_s = trinity_score   # score para threshold (ajustado sazonalmente)
        try:
            from seasonality_engine import get_seasonality_engine as _get_sea_btc
            _sea_res_btc     = _get_sea_btc().apply_to_score(trinity_score)
            trinity_score_s  = _sea_res_btc["score_adjusted"]
            _seasonal_result_btc = _sea_res_btc
            _sea_adj = _sea_res_btc["adjustment_pct"]
            if abs(_sea_adj) >= 10:
                log.info(
                    f"   🌐 Sazonalidade: {_sea_adj:+.1f}% "
                    f"({_sea_res_btc['multiplier']['day_label']} · "
                    f"{_sea_res_btc['multiplier']['hour_label']}) "
                    f"→ {trinity_score} → {trinity_score_s}"
                )
        except Exception as _sea_e:
            log.warning(f"[SEASONALITY] Falha ao aplicar multiplicador: {_sea_e}")

        # Filtro IPM (Cap. 4): se pressão < 40, entra no Invincible Mode
        # mas ainda salva estado para o dashboard
        ipm_blocked  = valid and not ipm_ok and not lateral
        direction_ok = (
            pressure_data["direction"] == "NEUTRAL" or
            pressure_data["direction"] == inst.get("direction", "").replace("LONG", "BULLISH").replace("SHORT", "BEARISH") or
            not ipm_ok   # sem filtro IPM ativo, não bloqueia por direção
        )

        # ── Invincible Mode ────────────────────────────────────────────────
        if not valid or lateral or (trinity_score_s < INST_SCORE_THRESHOLD):
            # Mesmo sem sinal, salva estado para o dashboard exibir
            atr_ns = regime_data.get("atr", price * 0.005)
            e_ns, s_ns, t1_ns, t2_ns, t3_ns = _calc_levels(price, inst.get("direction", "LONG"), atr_ns)
            _write_dashboard_state(
                price, inst, ms_data, volume_data, trend_data,
                corr_data, regime_data, e_ns, s_ns, t1_ns, t2_ns, t3_ns,
                smc_signal=smc_signal, mm_data=mm_data,
                pressure_data=pressure_data, rare_data=rare_data,
                trinity_score=trinity_score, geo_data=geo_data, cycle_data=cycle_data,
                direction_data=direction_data, neural_data=neural_data,
            )
            if not valid:
                log.info(f"💤 Invincible Mode: apenas {inst['confluences']}/6 confluências — sem sinal")
                blocked = "confluencias"
            elif lateral:
                log.info(f"💤 Mercado lateral ({struct}) — sem sinal institucional")
                blocked = "lateral"
            elif ipm_blocked:
                log.info(f"💤 IPM bloqueou: pressão {pressure_data['pressure']:+.1f} < ±40 — sem sinal")
                blocked = "score_baixo"
            else:
                log.info(f"💤 Trinity Score {trinity_score} abaixo do threshold {INST_SCORE_THRESHOLD} — sem sinal")
                blocked = "score_baixo"

            # alerts.send_status_update(...)  # DESATIVADO: redundante com BTC Regime Engine
            log.info("💤 Status calculado, sem sinal (Telegram desativado).")
            return

        # ── Calcula níveis ─────────────────────────────────────────────────
        direction = inst["direction"]
        atr       = regime_data.get("atr", price * 0.005)
        entry, stop, tp1, tp2, tp3 = _calc_levels(price, direction, atr)

        # ── Funding Rate Gate (FundingRateManager) ──────────────────────────
        # Usa funding_rate_raw já presente em deriv_data — sem chamada extra de API.
        log.info("💸 [18] Funding Rate Gate...")
        _fm = _get_funding_manager()
        funding_ok, funding_score, funding_reason = _fm.validate_from_deriv_data(
            deriv_data = deriv_data,
            direction  = direction,
            exchange   = "mexc",
        )
        log.info(
            f"   Funding: {'✅ LIBERADO' if funding_ok else '❌ BLOQUEADO'} "
            f"| score={funding_score:+d} | {funding_reason}"
        )

        if not funding_ok:
            log.info(f"❌ Funding bloqueou {direction} — sinal cancelado")
            _write_dashboard_state(
                price, inst, ms_data, volume_data, trend_data,
                corr_data, regime_data, entry, stop, tp1, tp2, tp3,
                smc_signal=smc_signal, mm_data=mm_data,
                pressure_data=pressure_data, rare_data=rare_data,
                trinity_score=trinity_score, geo_data=geo_data,
                cycle_data=cycle_data, direction_data=direction_data,
                neural_data=neural_data,
            )
            # alerts.send_status_update(...)  # DESATIVADO: redundante com BTC Regime Engine
            log.info("💤 Funding bloqueou — status calculado (Telegram desativado).")
            return

        # ── High Conviction Filter (Etapa 2) ──────────────────────────────
        log.info("🎯 [19] High Conviction Filter...")
        conviction_tier = "BLOCKED"
        try:
            _hcf        = _get_hcf()
            _hcf_signal = {"direction": direction, "score": trinity_score_s}
            _hcf_eval   = _hcf.evaluate(_hcf_signal, smc_signal or {})
            conviction_tier = _hcf_eval.get("conviction_tier", "BLOCKED")
            log.info(
                f"   HCF: {'✅ APROVADO' if _hcf_eval['approved'] else '❌ BLOQUEADO'} "
                f"| tier={conviction_tier} "
                f"| motivo={_hcf_eval.get('rejection_reason') or 'OK'}"
            )
            if not _hcf_eval["approved"]:
                _hcf.log_rejection(_hcf_signal, _hcf_eval)
                log.info(f"🚫 HCF bloqueou {direction} — sinal não enviado ao Telegram")
                _write_dashboard_state(
                    price, inst, ms_data, volume_data, trend_data,
                    corr_data, regime_data, entry, stop, tp1, tp2, tp3,
                    smc_signal=smc_signal, mm_data=mm_data,
                    pressure_data=pressure_data, rare_data=rare_data,
                    trinity_score=trinity_score, geo_data=geo_data,
                    cycle_data=cycle_data, direction_data=direction_data,
                    neural_data=neural_data,
                )
                # alerts.send_status_update(...)  # DESATIVADO: redundante com BTC Regime Engine
                log.info("💤 HCF bloqueou — status calculado (Telegram desativado).")
                return
        except Exception as _hcf_err:
            log.warning(f"   HCF lançou exceção (fail-closed): {_hcf_err}")
            return  # fail-closed: qualquer erro no HCF bloqueia o sinal

        rare_tag = f" ⭐ {rare_data['setup_type']}" if rare_data["rare_setup"] else ""
        log.info(
            f"🔔 SINAL INSTITUCIONAL: {direction} | Trinity {trinity_score} "
            f"| Funding {funding_score:+d} | tier={conviction_tier} "
            f"| Entry ${entry:,.2f} | Stop ${stop:,.2f}{rare_tag}"
        )

        _write_dashboard_state(
            price, inst, ms_data, volume_data, trend_data,
            corr_data, regime_data, entry, stop, tp1, tp2, tp3,
            smc_signal=smc_signal, mm_data=mm_data,
            pressure_data=pressure_data, rare_data=rare_data,
            trinity_score=trinity_score, geo_data=geo_data, cycle_data=cycle_data,
            direction_data=direction_data, neural_data=neural_data,
        )
        log.info("💾 Estado salvo no dashboard.")

        # ── News Sentinel (F3) — verifica clearance macro antes do Telegram ──
        _news_clearance  = {"cleared": True, "multiplier": 1.0, "context": None, "locked_until": None}
        _macro_context   = None
        trinity_score_n  = trinity_score_s   # score final = seasonal × news
        try:
            from news_sentinel import get_sentinel as _get_sentinel
            _news_clearance = _get_sentinel().check_signal_clearance(direction, trinity_score_s)
            if not _news_clearance["cleared"]:
                log.info(
                    f"[NEWS] 🔒 Sinal bloqueado por macro_lock "
                    f"até {_news_clearance['locked_until']} — não enviado ao Telegram"
                )
                # alerts.send_status_update(...)  # DESATIVADO: redundante com BTC Regime Engine
                log.info("💤 Macro lock ativo — status calculado (Telegram desativado).")
                return
            # Aplicar multiplicador macro ao score_final
            _news_mult = _news_clearance["multiplier"]
            trinity_score_n = round(trinity_score_s * _news_mult, 1)
            if abs(_news_mult - 1.0) >= 0.05:
                log.info(
                    f"   📰 News multiplier: ×{_news_mult} → {trinity_score_s} → {trinity_score_n}"
                )
            _macro_context = _news_clearance.get("context")
        except Exception as _news_e:
            log.warning(f"[NEWS] Falha no check_signal_clearance (fail-open): {_news_e}")

        alerts.send_institutional_signal(
            price            = price,
            symbol           = SYMBOL,
            timeframe        = TIMEFRAME,
            inst_score       = inst,
            market_structure = ms_data,
            volume_data      = volume_data,
            trend_data       = trend_data,
            correlation_data = corr_data,
            regime_data      = regime_data,
            derivatives_data = deriv_data,
            liquidations_data= liq_data,
            mtf_confluence   = mtf,
            entry            = entry,
            stop             = stop,
            tp1              = tp1,
            tp2              = tp2,
            tp3              = tp3,
            mm_data          = mm_data,
            pressure_data    = pressure_data,
            rare_data        = rare_data,
            trinity_score    = trinity_score,
            geo_data         = geo_data,
            cycle_data       = cycle_data,
            direction_data   = direction_data,
            neural_data      = neural_data,
            funding_score    = funding_score,
            seasonal_data    = _seasonal_result_btc,
            news_context     = _macro_context,
        )
        log.info("✅ Sinal institucional enviado ao Telegram.\n")

        # ── OutcomeTracker: registra sinal para tracking automático ────────
        try:
            _layer_scores_ot = (smc_signal or {}).get("score", {}).get("layer_scores", {})
            _ot = _get_tracker()
            _signal_ot = {
                "direction":       direction,
                "score":           trinity_score,
                "entry_price":     entry,
                "stop_loss":       stop,
                "tp1":             tp1,
                "tp2":             tp2,
                "symbol":          SYMBOL,
                "timestamp":       datetime.now(timezone.utc).isoformat(),
                "conviction_tier": conviction_tier,
                "layer_scores":    _layer_scores_ot,
                "source":          "institutional",
            }
            _sid = _ot.register_signal(_signal_ot)
            log.info(f"   OutcomeTracker: sinal {_sid[:8]} registrado para tracking.")
        except Exception as _ot_err:
            log.warning(f"   OutcomeTracker falhou ao registrar: {_ot_err}")

        # Log em arquivo
        log_entry = {
            "timestamp":    datetime.now().isoformat(),
            "type":         "institutional",
            "price":        price,
            "direction":    direction,
            "inst_score":   score,
            "confluences":  inst["confluences"],
            "trinity_score": trinity_score,
            "funding_score": funding_score,
            "funding_reason": funding_reason,
            "entry":        entry,
            "stop":         stop,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
        }
        with open(f"logs/institutional_{datetime.now().strftime('%Y%m%d')}.jsonl", "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    except Exception as e:
        log.error(f"💥 ERRO (institucional): {e}", exc_info=True)
        alerts.send_error(f"[INSTITUCIONAL] {e}")


def run_pump_radar():
    """
    Tarefa 4: Pump Radar — detecta pump institucional antes de acontecer.
    Scan de altcoins + alertas Telegram para candidatos com score >= 70.
    """
    log.info("🚀 [PUMP RADAR] Scan de altcoins para pump institucional...")
    try:
        result = _run_pump_cycle()
        candidates = result.get("candidates", [])
        log.info(
            f"[PumpRadar] {result.get('coins_scanned', 0)} coins scaneadas | "
            f"{len(candidates)} candidatos qualificados | "
            f"duração: {result.get('scan_duration_s', 0):.1f}s"
        )
        if candidates:
            top = candidates[0]
            log.info(
                f"[PumpRadar] Top: {top.get('symbol')} | "
                f"score {top.get('opportunity_score', 0):.0f} | "
                f"{top.get('move_classification', '?')} | "
                f"+{top.get('expected_move_pct', 0):.1f}%"
            )
        log.info("✅ Pump Radar concluído.\n")
    except Exception as e:
        log.error(f"💥 ERRO (pump_radar): {e}", exc_info=True)


def run_crash_radar():
    """
    Tarefa 5: Crash Radar — detecta crash institucional antes de acontecer.
    Scan de altcoins + alertas Telegram para candidatos com score >= 75.
    """
    log.info("📉 [CRASH RADAR] Scan de altcoins para crash iminente...")
    try:
        result = _run_crash_cycle()
        candidates = result.get("candidates", [])
        log.info(
            f"[CrashRadar] {result.get('coins_scanned', 0)} coins scaneadas | "
            f"{len(candidates)} candidatos qualificados | "
            f"duração: {result.get('scan_duration_s', 0):.1f}s"
        )
        if candidates:
            top = candidates[0]
            log.info(
                f"[CrashRadar] Top: {top.get('symbol')} | "
                f"score {top.get('opportunity_score', 0):.0f} | "
                f"{top.get('move_classification', top.get('crash_classification', '?'))} | "
                f"-{top.get('expected_move_pct', 0):.1f}%"
            )
        log.info("✅ Crash Radar concluído.\n")
    except Exception as e:
        log.error(f"💥 ERRO (crash_radar): {e}", exc_info=True)


if __name__ == "__main__":
    log.info("🤖 Agente de IA para Futuros MEXC iniciado!")
    _liq_start()   # inicia WebSocket Binance liquidações em background
    log.info(f"   Par: {SYMBOL} | Timeframe: {TIMEFRAME} | Score threshold: {SCORE_THRESHOLD}")
    log.info(f"   Resumo (sem IA): a cada {SUMMARY_INTERVAL_MINUTES} min")
    log.info(f"   Sinal completo (com Claude): a cada {SIGNAL_INTERVAL_MINUTES} min")

    # Resumo imediato ao iniciar (sem Claude)
    run_analysis_summary()

    # Tarefa 1: a cada SUMMARY_INTERVAL_MINUTES — análise completa, só send_summary
    schedule.every(SUMMARY_INTERVAL_MINUTES).minutes.do(run_analysis_summary)

    # Tarefa 2: a cada SIGNAL_INTERVAL_MINUTES — análise + Claude, send_signal quando acima do threshold
    schedule.every(SIGNAL_INTERVAL_MINUTES).minutes.do(run_analysis_signal)

    # Tarefa 3: análise institucional multi-camadas (Smart Money / Invincible Mode)
    run_institutional_analysis()
    schedule.every(INST_INTERVAL_MINUTES).minutes.do(run_institutional_analysis)
    log.info(f"🏛️  Análise institucional agendada a cada {INST_INTERVAL_MINUTES} min")

    # Tarefa 4: Pump Radar a cada 30min (detecta pump antes de acontecer)
    run_pump_radar()
    schedule.every(30).minutes.do(run_pump_radar)
    log.info("🚀 Pump Radar agendado a cada 30 min")

    # Morning Brief diário às 8h (horário local do servidor — use BRT)
    schedule.every().day.at("08:00").do(run_morning_brief)
    log.info("☀️ Morning Brief agendado para 08:00 BRT")

    # Cycle Intelligence semanal — toda segunda às 08:05
    schedule.every().monday.at("08:05").do(run_cycle_intelligence)
    log.info("🔄 Cycle Intelligence agendado para toda segunda às 08:05")

    log.info("⏰ Pressione Ctrl+C para parar.")

    while True:
        schedule.run_pending()
        time.sleep(30)
