"""
main.py — ORQUESTRADOR PRINCIPAL
Roda todos os módulos na sequência correta e envia alertas.
Execute com: python main.py
"""
import schedule
import time
import json
import logging
from datetime import datetime
from pathlib import Path

# Módulos do agente
from mexc_client    import get_ohlcv, get_current_price, get_orderbook
from indicators     import regime, trend, momentum, volume, derivatives, onchain, sentiment, liquidations
from indicators     import market_structure, correlation
from scoring        import calculate_score
from institutional_scoring import calculate_institutional_score
from smart_money_engine import SmartMoneyEngine
from market_maker_engine import run_market_maker_analysis
import agent
import alerts
from config         import (
    SYMBOL, TIMEFRAME, SCORE_THRESHOLD,
    SUMMARY_INTERVAL_MINUTES, SIGNAL_INTERVAL_MINUTES,
    INST_INTERVAL_MINUTES, INST_SCORE_THRESHOLD,
)
from morning_brief       import run_morning_brief
from cycle_intelligence import run_cycle_intelligence


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

        alerts.send_summary(price, score_data)
        log.info("✅ Resumo enviado ao Telegram.\n")

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

        # ── Invincible Mode ────────────────────────────────────────────────
        score    = inst["inst_score"]
        valid    = inst["valid"]
        struct   = ms_data.get("structure", "INDEFINIDA")
        lateral  = struct in ("LATERAL", "INDEFINIDA", "TRANSIÇÃO")

        if not valid or lateral or (score < INST_SCORE_THRESHOLD and score > (100 - INST_SCORE_THRESHOLD)):
            # Mesmo sem sinal, salva estado para o dashboard exibir
            atr_ns = regime_data.get("atr", price * 0.005)
            e_ns, s_ns, t1_ns, t2_ns, t3_ns = _calc_levels(price, inst.get("direction", "LONG"), atr_ns)
            _write_dashboard_state(
                price, inst, ms_data, volume_data, trend_data,
                corr_data, regime_data, e_ns, s_ns, t1_ns, t2_ns, t3_ns,
                smc_signal=smc_signal, mm_data=mm_data,
            )
            if not valid:
                log.info(f"💤 Invincible Mode: apenas {inst['confluences']}/6 confluências — sem sinal")
            elif lateral:
                log.info(f"💤 Mercado lateral ({struct}) — sem sinal institucional")
            else:
                log.info(f"💤 Score {score} abaixo do threshold {INST_SCORE_THRESHOLD} — sem sinal")
            return

        # ── Calcula níveis ─────────────────────────────────────────────────
        direction = inst["direction"]
        atr       = regime_data.get("atr", price * 0.005)
        entry, stop, tp1, tp2, tp3 = _calc_levels(price, direction, atr)

        log.info(f"🔔 SINAL INSTITUCIONAL: {direction} | Score {score} | "
                 f"Entry ${entry:,.2f} | Stop ${stop:,.2f}")

        _write_dashboard_state(
            price, inst, ms_data, volume_data, trend_data,
            corr_data, regime_data, entry, stop, tp1, tp2, tp3,
            smc_signal=smc_signal, mm_data=mm_data,
        )
        log.info("💾 Estado salvo no dashboard.")

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
        )
        log.info("✅ Sinal institucional enviado ao Telegram.\n")

        # Log em arquivo
        log_entry = {
            "timestamp":  datetime.now().isoformat(),
            "type":       "institutional",
            "price":      price,
            "direction":  direction,
            "inst_score": score,
            "confluences": inst["confluences"],
            "entry":      entry,
            "stop":       stop,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
        }
        with open(f"logs/institutional_{datetime.now().strftime('%Y%m%d')}.jsonl", "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    except Exception as e:
        log.error(f"💥 ERRO (institucional): {e}", exc_info=True)
        alerts.send_error(f"[INSTITUCIONAL] {e}")


if __name__ == "__main__":
    log.info("🤖 Agente de IA para Futuros MEXC iniciado!")
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
