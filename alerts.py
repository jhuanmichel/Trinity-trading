"""
alerts.py — Sistema de Alertas via Telegram
Envia sinais formatados e profissionais para o seu chat.
"""
import asyncio
import requests
import json
import time
from datetime import datetime
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, SYMBOL, TIMEFRAME, SCORE_THRESHOLD, INST_INTERVAL_MINUTES

# ── Cooldown para mensagens de status/resumo (evita spam sem sinal) ───────────
_last_status_ts:  float = 0.0
_last_summary_ts: float = 0.0
STATUS_COOLDOWN_H  = 4   # status "sem sinal" no máximo a cada 4h
SUMMARY_COOLDOWN_H = 6   # "Atualização do Agente" no máximo a cada 6h


def send_message(text: str) -> bool:
    """Envia mensagem de texto para o Telegram."""
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Erro Telegram: {e}")
        return False


def send_signal(price: float, score_data: dict, ai_analysis: dict, analyses: dict) -> bool:
    """
    Formata e envia o sinal completo para o Telegram.
    Só envia se o score ultrapassar o limiar configurado.
    """
    signal    = score_data.get("signal", "NO_TRADE")
    prob_long = score_data.get("prob_long", 50)
    ai_dir    = ai_analysis.get("direcao", "NO_TRADE")
    forca     = ai_analysis.get("forca_sinal", 0)

    # Não envia NO_TRADE ou sinais fracos
    if signal == "NO_TRADE" and ai_dir == "NO_TRADE":
        print(f"⏭️  Sinal: NO TRADE — nenhum alerta enviado")
        return False

    if forca < 0:
        print(f"⏭️  Força do sinal {forca}/10 < 5 — nenhum alerta enviado")
        return False

    # Emojis de acordo com direção
    if ai_dir == "LONG" or signal == "LONG":
        emoji_dir = "🟢📈"
        emoji_sinal = "LONG"
    elif ai_dir == "SHORT" or signal == "SHORT":
        emoji_dir = "🔴📉"
        emoji_sinal = "SHORT"
    else:
        emoji_dir = "⚪️"
        emoji_sinal = "AGUARDANDO"

    # Nível de risco
    risco     = ai_analysis.get("risco_nivel", "?")
    risco_emoji = {"BAIXO": "🟢", "MÉDIO": "🟡", "ALTO": "🔴"}.get(risco, "⚪️")

    now = datetime.now().strftime("%d/%m %H:%M")

    # Dados dos módulos para resumo
    regime   = analyses.get("regime", {})
    trend    = analyses.get("trend", {})
    mom      = analyses.get("momentum", {})
    deriv    = analyses.get("derivatives", {})
    liq      = analyses.get("liquidations", {})
    sent     = analyses.get("sentiment", {})

    msg = f"""
{'━'*32}
{emoji_dir} <b>SINAL DETECTADO — MEXC FUTUROS</b>
{'━'*32}
📊 Par: <b>{SYMBOL}</b> | ⏱ TF: {TIMEFRAME}
🕐 {now}

💵 Preço: <b>${price:,.2f}</b>
🎯 Direção: <b>{emoji_sinal}</b>
💪 Força: <b>{forca}/10</b> | Score: <b>{score_data.get('final_score', 50):.1f}/100</b>
📊 LONG {prob_long:.0f}% vs SHORT {score_data.get('prob_short', 50):.0f}%

{'━'*32}
<b>🎯 NÍVEIS DE TRADE:</b>
🟡 Entrada:    <b>${ai_analysis.get('entrada_ideal', '?')}</b>
🛑 Stop Loss:  <b>${ai_analysis.get('stop_loss', '?')}</b>
🎯 Alvo 1:     <b>${ai_analysis.get('take_profit_1', '?')}</b>
🎯 Alvo 2:     <b>${ai_analysis.get('take_profit_2', '?')}</b>
📐 R/R:        <b>{ai_analysis.get('risco_retorno', '?')}</b>
{risco_emoji} Risco:      <b>{risco}</b>

{'━'*32}
<b>📈 ANÁLISE DOS MÓDULOS:</b>
• <b>Regime:</b> {regime.get('regime', '?')} (ADX {regime.get('adx', '?')})
• <b>Tendência:</b> {trend.get('ema_signal', '?')}
• <b>Momentum:</b> RSI {mom.get('rsi', '?')} — {mom.get('rsi_signal', '?')}
• <b>Derivativos:</b> Funding {deriv.get('funding_rate', '?')}% | L/S {deriv.get('long_pct', '?')}/{deriv.get('short_pct', '?')}
• <b>Liquidações:</b> {liq.get('liq_signal', 'N/A')} | Longs ${liq.get('liq_1h_long_usd', 0):.1f}M / Shorts ${liq.get('liq_1h_short_usd', 0):.1f}M (1h)
• <b>Sentimento:</b> F&G {sent.get('fg_value', '?')}/100 — {sent.get('fg_label', '?')}

{'━'*32}
<b>🧠 ANÁLISE DA IA:</b>
{ai_analysis.get('justificativa', 'N/A')}

<b>✅ Confirma se:</b> {ai_analysis.get('melhor_cenario', 'N/A')}
<b>❌ Invalida se:</b> {ai_analysis.get('pior_cenario', 'N/A')}
"""

    # Alertas importantes (divergências etc.)
    alertas = ai_analysis.get("alertas", [])
    if alertas:
        msg += f"\n<b>⚠️ ALERTAS:</b>\n"
        for alerta in alertas:
            msg += f"• {alerta}\n"

    # Mapa de liquidações — mostra clusters se disponíveis
    heatmap = liq.get("heatmap_summary", "")
    if heatmap and "indisponível" not in heatmap.lower():
        msg += f"\n<b>🗺️ MAPA DE LIQUIDAÇÕES:</b>\n<code>{heatmap}</code>\n"

    msg += f"\n{'━'*32}"
    msg += "\n⚠️ <i>Apenas análise — não é recomendação de investimento.</i>"

    return send_message(msg.strip())


def send_summary(price: float, score_data: dict) -> bool:
    """Envia resumo periódico mesmo sem sinal (para monitoramento).
    Respeitado cooldown de SUMMARY_COOLDOWN_H horas para evitar spam."""
    global _last_summary_ts
    now_ts = time.time()
    if (now_ts - _last_summary_ts) < SUMMARY_COOLDOWN_H * 3600:
        return False  # ainda no cooldown — silencia
    _last_summary_ts = now_ts

    now = datetime.now().strftime("%d/%m %H:%M")
    msg = f"""
📊 <b>Atualização do Agente</b> — {now}
💵 {SYMBOL}: <b>${price:,.2f}</b>
📈 Score: {score_data.get('final_score', 50):.1f}/100
🔮 LONG {score_data.get('prob_long', 50):.0f}% vs SHORT {score_data.get('prob_short', 50):.0f}%
💤 Sinal: {score_data.get('signal', 'NO TRADE')} (confiança: {score_data.get('confidence', '?')})
"""
    return send_message(msg.strip())


def send_error(error_msg: str) -> bool:
    """Notifica erros críticos."""
    msg = f"🚨 <b>ERRO NO AGENTE</b>\n{error_msg}"
    return send_message(msg)


# ─────────────────────────────────────────────────────────────────────────────
# SINAL INSTITUCIONAL MULTI-CAMADAS
# ─────────────────────────────────────────────────────────────────────────────

def send_institutional_signal(
    price: float,
    symbol: str,
    timeframe: str,
    inst_score: dict,
    market_structure: dict,
    volume_data: dict,
    trend_data: dict,
    correlation_data: dict,
    regime_data: dict,
    derivatives_data: dict,
    liquidations_data: dict,
    mtf_confluence: dict,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    tp3: float,
    mm_data:        dict = None,
    pressure_data:  dict = None,
    rare_data:      dict = None,
    trinity_score:  float = None,
    geo_data:       dict = None,
    cycle_data:     dict = None,
    direction_data: dict = None,
    neural_data:    dict = None,
    funding_score:  int = None,
) -> bool:
    """
    Envia alerta institucional no formato Smart Money com todos os detalhes das 7 camadas.
    Só deve ser chamado quando inst_score['valid'] == True.
    """
    direction  = inst_score.get("direction", "AGUARDANDO")
    score      = inst_score.get("inst_score", 50)
    signal     = inst_score.get("signal", "NO SIGNAL")
    strength   = inst_score.get("strength", "")
    confluences = inst_score.get("confluences", 0)
    confidence_pct = min(99, round(score * 0.97 + confluences * 0.5))

    # Emojis por direção
    if "LONG" in direction:
        dir_emoji  = "🟢📈"
        bias_color = "🟢"
    elif "SHORT" in direction:
        dir_emoji  = "🔴📉"
        bias_color = "🔴"
    else:
        dir_emoji  = "⚪️"
        bias_color = "⚪️"

    # Nível de força
    strength_map = {
        "EXTREMO":    "🔥🔥🔥 EXTREMAMENTE FORTE",
        "FORTE":      "💪💪 FORTE",
        "MODERADO":   "📊 MODERADO",
        "NEUTRO":     "⚪️ NEUTRO",
        "INSUFICIENTE": "❌ INSUFICIENTE",
    }
    strength_label = strength_map.get(strength, strength)

    # Risco (baseado no R/R)
    rr = round((tp1 - entry) / (entry - stop), 2) if entry != stop else 0
    risk_label = "BAIXO" if rr >= 2.0 else "MÉDIO" if rr >= 1.5 else "ALTO"
    risk_emoji = {"BAIXO": "🟢", "MÉDIO": "🟡", "ALTO": "🔴"}.get(risk_label, "⚪️")

    # MTF confluence
    mtf_line = ""
    if mtf_confluence:
        agreed = mtf_confluence.get("agreed_timeframes", [])
        total  = mtf_confluence.get("total_timeframes", 4)
        mtf_line = f"\n🕐 <b>MTF ({len(agreed)}/{total} TFs confirmam):</b> {', '.join(agreed)}"

    # Funding Gate — linha para o Telegram
    _fs_map = {
        2:  "✅✅ Confirma Forte (+2)",
        1:  "✅ Favorável (+1)",
        0:  "⚠️ Neutro (size 60%)",
    }
    funding_line = (
        f"\n💸 Funding Gate: <b>{_fs_map.get(funding_score, f'score {funding_score:+d}')}</b>"
        if funding_score is not None else ""
    )

    now = datetime.now().strftime("%d/%m %H:%M")

    msg = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{dir_emoji} <b>SINAL INSTITUCIONAL</b> — {symbol}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 TF: {timeframe}  |  🕐 {now}
💵 Preço: <b>${price:,.2f}</b>

{bias_color} Bias: <b>{direction}</b>
🏆 Score: <b>{score}/100</b>
💡 Confiança: <b>{confidence_pct}%</b>
⚡ Força: <b>{strength_label}</b>
🔗 Confluências: <b>{confluences}/6 camadas</b>{funding_line}
{mtf_line}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>📐 NÍVEIS DE TRADE:</b>
🟡 Entry:   <b>${entry:,.2f}</b>
🛑 Stop:    <b>${stop:,.2f}</b>
🎯 TP1:     <b>${tp1:,.2f}</b>
🎯 TP2:     <b>${tp2:,.2f}</b>
🎯 TP3:     <b>${tp3:,.2f}</b>
📐 R/R:     <b>1:{rr}</b>
{risk_emoji} Risco:   <b>{risk_label}</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>🏛️ ANÁLISE POR CAMADA:</b>
📊 Market Structure: <b>{market_structure.get('structure', '?')}</b>{' — BOS BULL 🟢' if market_structure.get('bos_bull') else ' — BOS BEAR 🔴' if market_structure.get('bos_bear') else ''}{' — CHOCH ⚡' if market_structure.get('choch') else ''}
💧 Liquidity: Sweep {'↓abaixo 🟢' if market_structure.get('sweep_low') else '↑acima 🔴' if market_structure.get('sweep_high') else '—'} | EQL: {'SIM ⚠️' if market_structure.get('equal_lows') else 'não'} | EQH: {'SIM ⚠️' if market_structure.get('equal_highs') else 'não'}
📦 Volume: {'Forte ✅' if volume_data.get('high_volume') else 'Normal'} | CVD {'↑' if volume_data.get('cvd_trending_up') else '↓'} | {'Saudável ✅' if volume_data.get('healthy_move') else '⚠️ ARMADILHA' if volume_data.get('trap_signal') else ''}
📈 Trend: {trend_data.get('ema_signal', '?')} | Ichimoku {'acima' if trend_data.get('ichimoku_above_cloud') else 'abaixo' if trend_data.get('ichimoku_below_cloud') else 'dentro'} nuvem
🌍 Macro: {correlation_data.get('bias', '?')} | BTC {correlation_data.get('btc_change', 0):+.1f}% | ETH {correlation_data.get('eth_change', 0):+.1f}% | BTC.D {correlation_data.get('btc_dominance', 0):.1f}%
🌋 Volatilidade: {regime_data.get('regime', '?')} | ATR {regime_data.get('atr_pct', 0):.2f}%{'| 🔥 SQUEEZE' if regime_data.get('squeeze') else ''}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ <i>Apenas análise — não é recomendação de investimento.</i>"""

    # ── Seção Market Maker (se disponível) ───────────────────────────────────
    if mm_data:
        mm_bias   = mm_data.get("bias", "neutral").upper()
        mm_score  = mm_data.get("market_maker_score", 0)
        mm_conf   = mm_data.get("confidence", 0)
        trap_prob = mm_data.get("trap_probability", 0)
        trap_dir  = mm_data.get("trap_direction", "NEUTRO")
        sweep_str = mm_data.get("sweep_strength", 0)
        sweep_bias= mm_data.get("sweep_bias", "NEUTRO")
        liq_hi    = mm_data.get("liquidity_target_high")
        liq_lo    = mm_data.get("liquidity_target_low")
        premium   = mm_data.get("premium_zone", False)
        discount  = mm_data.get("discount_zone", False)
        eq_zone   = mm_data.get("equilibrium_zone", False)
        pos_pct   = mm_data.get("range_position_pct", 50)
        inst_bias = mm_data.get("institutional_bias", "NEUTRAL")
        trap_sigs = mm_data.get("trap_signals", [])

        zone_label = "🔴 PREMIUM"  if premium else "🟢 DISCOUNT" if discount else "🟡 EQUILIBRIUM"
        trap_emoji = "🚨" if trap_prob >= 60 else "⚠️" if trap_prob >= 35 else "✅"
        sweep_emoji = "🟢" if sweep_bias == "BULLISH" else "🔴" if sweep_bias == "BEARISH" else "⚪️"

        mm_section = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>🏦 MARKET MAKER ENGINE:</b>
📊 MM Score: <b>{mm_score}/100</b>  |  Bias: <b>{mm_bias}</b>  |  Conf: <b>{mm_conf:.0f}%</b>
🌐 MTF Institutional: <b>{inst_bias}</b>
{zone_label}  |  Posição no range: <b>{pos_pct:.1f}%</b>
💧 Liq. Alvo Alto: <b>${liq_hi:,.2f}</b>  |  Baixo: <b>${liq_lo:,.2f}</b>
{sweep_emoji} Sweep: <b>{sweep_bias}</b> (força {sweep_str}/100)
{trap_emoji} Trap: <b>{trap_prob}%</b>{f' — {trap_dir}' if trap_dir != 'NEUTRO' else ''}"""

        if trap_sigs:
            mm_section += f"\n⚡ {' | '.join(trap_sigs[:2])}"

        msg = msg.rstrip() + mm_section

    # ── Seção IPM + Setup Raro (Cap. 3 e 4) ──────────────────────────────────
    has_trinity = pressure_data or rare_data or trinity_score is not None
    if has_trinity:
        p_val  = pressure_data.get("pressure", 0)      if pressure_data else 0
        p_dir  = pressure_data.get("direction", "NEUTRAL") if pressure_data else "NEUTRAL"
        p_ok   = pressure_data.get("filter_passed", False) if pressure_data else False
        p_bar  = "🟢" if p_val >= 40 else "🔴" if p_val <= -40 else "🟡"

        r_ok   = rare_data.get("rare_setup", False)    if rare_data else False
        r_sc   = rare_data.get("score", 0)             if rare_data else 0
        r_type = rare_data.get("setup_type", "NENHUM") if rare_data else "NENHUM"
        r_facs = rare_data.get("factors_active", [])   if rare_data else []
        r_star = "⭐" if r_ok else "○"

        ts_str = f"<b>{trinity_score:.1f}/100</b>" if trinity_score is not None else "—"

        trinity_section = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>🔬 TRINITY SCORE: {ts_str}</b>
{p_bar} IPM: <b>{p_val:+.0f}</b> ({p_dir}) {'✅ filtro OK' if p_ok else '❌ pressão insuficiente'}
{r_star} Setup: <b>{r_type}</b> ({r_sc:.0f}/100)"""
        if r_facs:
            trinity_section += f"\n   Fatores: {' · '.join(r_facs)}"

        msg = msg.rstrip() + trinity_section

    # ── Seção Geopolitical Intelligence ──────────────────────────────────────
    if geo_data:
        g_score = geo_data.get("geo_score",         50.0)
        g_bias  = geo_data.get("geo_bias",          "NEUTRAL")
        g_conf  = geo_data.get("geo_confidence",    0.0)
        g_risk  = geo_data.get("risk_sentiment",    "NEUTRAL")
        g_liq   = geo_data.get("liquidity_outlook", "NEUTRAL")
        g_top   = geo_data.get("top_event",         "N/A")
        g_src   = geo_data.get("top_event_source",  "N/A")
        g_cat   = geo_data.get("top_event_category","MARKET")
        g_rare  = geo_data.get("rare_macro_setup",  False)
        g_combo = geo_data.get("rare_macro_combo",  None)
        g_arts  = geo_data.get("article_count",     0)
        g_hi    = geo_data.get("high_impact_count", 0)

        # Emojis
        g_bias_emoji = "🟢" if g_bias == "BULLISH" else "🔴" if g_bias == "BEARISH" else "⚪️"
        risk_emoji   = {"RISK_ON": "🟢 RISK ON", "RISK_OFF": "🔴 RISK OFF", "NEUTRAL": "⚪️ NEUTRO"}.get(g_risk, g_risk)
        liq_emoji    = {"INCREASING": "📈 ↑ AUMENTANDO", "DECREASING": "📉 ↓ DIMINUINDO", "NEUTRAL": "➡️ ESTÁVEL"}.get(g_liq, g_liq)
        rare_line    = f"\n🚨 <b>RARE MACRO SETUP:</b> {g_combo}" if g_rare and g_combo else ("\n🚨 <b>RARE MACRO SETUP ATIVO</b>" if g_rare else "")

        # Limita título do evento
        g_top_short = (g_top[:70] + "...") if len(g_top) > 70 else g_top

        geo_section = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>🌍 GEOPOLITICAL INTELLIGENCE:</b>
{g_bias_emoji} Geo Score: <b>{g_score:.1f}/100</b>  |  Bias: <b>{g_bias}</b>  |  Conf: <b>{g_conf:.0f}%</b>
💰 Liquidez: {liq_emoji}
🎯 Risco:    {risk_emoji}
📰 Top Event [{g_cat}]: <i>{g_top_short}</i>
   Fonte: {g_src} | {g_arts} artigos ({g_hi} alto impacto){rare_line}"""

        msg = msg.rstrip() + geo_section

    # ── Seção Bitcoin Cycle Intelligence ─────────────────────────────────────
    if cycle_data:
        c_phase  = cycle_data.get("cycle_phase",        "ACCUMULATION")
        c_conf   = cycle_data.get("cycle_confidence",   0.0)
        c_str    = cycle_data.get("cycle_strength",     "WEAK")
        c_score  = cycle_data.get("cycle_score",        50.0)
        c_risk   = cycle_data.get("risk_level",         "MEDIUM")
        c_bias   = cycle_data.get("bias_adjustment",    "NEUTRAL")
        c_macro  = cycle_data.get("macro_trend",        "NEUTRAL")
        c_vol    = cycle_data.get("expected_volatility","MEDIUM")
        c_mult   = cycle_data.get("risk_multiplier",    1.0)
        c_rare   = cycle_data.get("rare_cycle_setup",   False)
        c_rtype  = cycle_data.get("rare_cycle_type",    None)
        c_pos    = cycle_data.get("cycle_position_pct", 50.0)
        c_desc   = cycle_data.get("phase_description",  "")

        # Sub-modelos
        halving  = cycle_data.get("halving",  {})
        trend_c  = cycle_data.get("trend",    {})
        onchain  = cycle_data.get("onchain",  {})

        # Emojis por fase
        phase_emoji = {
            "ACCUMULATION": "🟡", "EARLY_BULL": "🟢", "MID_BULL": "🟢",
            "LATE_BULL":    "🟠", "DISTRIBUTION": "🟠",
            "BEAR":         "🔴", "CAPITULATION": "🔴",
        }.get(c_phase, "⚪️")

        bias_emoji = {
            "AGGRESSIVE_LONG": "🚀", "FAVOR_LONG": "🟢",
            "NEUTRAL":         "⚪️", "REDUCE_LONGS": "🟡",
            "FAVOR_SHORT":     "🔴",
        }.get(c_bias, "⚪️")

        macro_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪️"}.get(c_macro, "⚪️")
        risk_emoji_c = {"MEDIUM": "🟡", "HIGH": "🟠", "VERY_HIGH": "🔴"}.get(c_risk, "⚪️")
        vol_emoji   = {"LOW": "😴", "MEDIUM": "📊", "HIGH": "🔥"}.get(c_vol, "📊")

        # Halving info
        h_phase    = halving.get("phase", "?")
        h_days     = halving.get("days_since", "?")
        h_age      = halving.get("cycle_age_pct", 0.0)

        # MVRV
        mvrv_val   = onchain.get("mvrv", None)
        mvrv_str   = f"MVRV: <b>{mvrv_val:.2f}</b>" if mvrv_val else "MVRV: N/A"

        # 200W MA
        above_200w = trend_c.get("above_200w_ma", None)
        ma200_str  = "200W MA: ✅" if above_200w else "200W MA: ❌" if above_200w is not None else "200W MA: ?"

        rare_c_line = f"\n★ <b>RARE CYCLE SETUP: {c_rtype}</b>" if c_rare and c_rtype else ""

        cycle_section = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>🧠 BITCOIN CYCLE INTELLIGENCE:</b>
{phase_emoji} Fase: <b>{c_phase.replace('_', ' ')}</b>  |  Score: <b>{c_score:.0f}/100</b>  |  Conf: <b>{c_conf:.0f}%</b>
{bias_emoji} Bias: <b>{c_bias.replace('_', ' ')}</b>  |  Risk Mult: <b>{c_mult}x</b>
{macro_emoji} Macro: <b>{c_macro}</b>  |  {risk_emoji_c} Risco: <b>{c_risk}</b>
{vol_emoji} Volatilidade: <b>{c_vol}</b>  |  Ciclo: <b>{c_pos:.0f}%</b> completo
⛏️ Halving: <b>{h_phase}</b>  |  {h_days}d desde último  |  {h_age:.0f}% do ciclo
🔗 {mvrv_str}  |  {ma200_str}{rare_c_line}"""

        msg = msg.rstrip() + cycle_section

    # ── Seção Institutional Direction Intelligence (Cap. 16) ───────────────────
    if direction_data:
        d_dir    = direction_data.get("direction",              "NEUTRAL")
        d_conf   = direction_data.get("direction_confidence",   50.0)
        d_score  = direction_data.get("direction_score",        50.0)
        d_break  = direction_data.get("breakout_probability",   30.0)
        d_fake   = direction_data.get("fake_move_probability",  30.0)
        d_dom    = direction_data.get("dominant_market",        "BALANCED")
        d_valid  = direction_data.get("move_validity",          "WEAK")
        d_mmdef  = direction_data.get("market_maker_defense",   False)
        d_mmlvl  = direction_data.get("defense_level",          None)
        d_mmstr  = direction_data.get("defense_strength",       0.0)
        d_mmtype = direction_data.get("defense_type",           None)
        d_ibias  = direction_data.get("institutional_bias",     "NEUTRAL")
        d_dabs   = direction_data.get("absorption_type",        None)
        d_fund   = direction_data.get("funding_bias",           "NEUTRAL")
        d_fext   = direction_data.get("funding_extreme",        False)
        d_fann   = direction_data.get("funding_annual_pct",     0.0)
        d_oi     = direction_data.get("oi_change_pct",          0.0)
        d_rare   = direction_data.get("rare_direction_setup",   False)
        d_rtype  = direction_data.get("rare_direction_type",    None)
        d_rstr   = direction_data.get("rare_direction_strength", 0.0)

        # Emojis
        dir_arrow   = {"UP": "↑", "DOWN": "↓"}.get(d_dir, "→")
        dir_emoji_d = {"UP": "🟢", "DOWN": "🔴", "NEUTRAL": "⚪️"}.get(d_dir, "⚪️")
        valid_emoji = {"REAL": "✅", "WEAK": "🟡", "FAKE": "❌"}.get(d_valid, "⚪️")
        fund_emoji  = {
            "BEARISH":          "🔴",
            "SLIGHTLY_BEARISH": "🟡",
            "NEUTRAL":          "⚪️",
            "SLIGHTLY_BULLISH": "🟡",
            "BULLISH":          "🟢",
        }.get(d_fund, "⚪️")

        mm_line = (
            f"🏦 MM Defense: <b>Detectado</b> | Nível: ${d_mmlvl:,.0f} | "
            f"Força: {d_mmstr:.0f}% | {d_mmtype}"
            if d_mmdef and d_mmlvl else
            "🏦 MM Defense: —"
        )
        abs_line = (
            f"🌊 Delta: <b>{d_ibias}</b> | Absorção: <b>{d_dabs}</b>"
            if d_dabs else
            f"🌊 Delta: <b>{d_ibias}</b>"
        )
        fund_ext_flag = " 🚨 EXTREMO" if d_fext else ""
        rare_dir_line = (
            f"\n★ <b>RARE SETUP: {d_rtype}</b> | força: {d_rstr:.0f}%"
            if d_rare and d_rtype else ""
        )

        direction_section = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>📊 INSTITUTIONAL DIRECTION INTELLIGENCE:</b>
{mm_line}
{abs_line}
⚖️ Perp vs Spot: <b>{d_dom}</b> | Move: {valid_emoji} <b>{d_valid}</b>
{dir_emoji_d} Direção: <b>{d_dir}{dir_arrow}</b> | Conf: <b>{d_conf:.0f}%</b> | Score: <b>{d_score:.0f}/100</b>
🎭 Fake Move: <b>{d_fake:.0f}%</b> | Breakout: <b>{d_break:.0f}%</b>
{fund_emoji} Funding: <b>{d_fund}{fund_ext_flag}</b> ({d_fann:+.1f}% a.a.) | OI: <b>{d_oi:+.1f}%</b>{rare_dir_line}"""

        msg = msg.rstrip() + direction_section

    # ── Seção Neural Intelligence Engine (Cap. 17) ────────────────────────────
    if neural_data:
        n_bull   = neural_data.get("bull_probability",                 20.0)
        n_bear   = neural_data.get("bear_probability",                 20.0)
        n_side   = neural_data.get("sideways_probability",             35.0)
        n_vexp   = neural_data.get("volatility_expansion_probability", 15.0)
        n_manip  = neural_data.get("manipulation_probability",         10.0)
        n_conf   = neural_data.get("confidence_score",                 20.0)
        n_regime = neural_data.get("market_regime",                    "RANGING")
        n_score  = neural_data.get("neural_score",                     50.0)
        n_bias   = neural_data.get("neural_bias",                      "NEUTRAL")
        n_bstr   = neural_data.get("bias_strength",                    0.0)
        n_edge   = neural_data.get("bull_edge",                        0.0)
        n_agree  = neural_data.get("agreement_score",                  50.0)
        n_dom    = neural_data.get("dominant_model",                   "unknown")
        n_rare   = neural_data.get("rare_neural_setup",                False)
        n_rtype  = neural_data.get("rare_neural_type",                 None)
        n_rstr   = neural_data.get("rare_neural_strength",             0.0)
        n_bal    = neural_data.get("is_balanced",                      True)
        n_calib  = neural_data.get("calibration_note",                 "")
        n_acc    = neural_data.get("retrain_accuracy",                 0.0)
        n_mconf  = neural_data.get("model_confidences",                {})

        # Emojis
        bias_emoji = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪️"}.get(n_bias, "⚪️")
        regime_emoji = {
            "TRENDING":             "📈",
            "RANGING":              "↔️",
            "REVERSAL":             "🔄",
            "ACCUMULATION":         "🟡",
            "DISTRIBUTION":         "🟠",
            "VOLATILITY_EXPANSION": "💥",
            "MANIPULATION":         "🎭",
        }.get(n_regime, "❓")

        # Barra de probabilidade visual (total ~100)
        def _pbar(v, total=100): return "█" * int(v / 10) + "░" * (10 - int(v / 10))

        # Modelos confiança compacta
        mconf_str = ""
        if n_mconf:
            parts = [f"{k[:3].upper()}:{v:.0f}" for k, v in n_mconf.items()]
            mconf_str = f"\n🔬 Modelos: {' | '.join(parts)}"

        # Rare setup neural
        rare_neural_line = ""
        if n_rare and n_rtype:
            rare_emojis = {
                "ACCUMULATION_WHALE_BUYING": "🐋",
                "DISTRIBUTION_ABSORPTION":   "🕳️",
                "COMPRESSION_VOL_EXPANSION": "💥",
            }
            r_emoji = rare_emojis.get(n_rtype, "★")
            rare_neural_line = f"\n{r_emoji} <b>RARE NEURAL: {n_rtype.replace('_', ' ')}</b> | força: {n_rstr:.0f}%"

        balance_note = " ⚠️ desequilíbrio" if not n_bal else ""

        neural_section = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>🧠 NEURAL INTELLIGENCE v2:</b>
{regime_emoji} Regime: <b>{n_regime.replace('_', ' ')}</b>  |  Score: <b>{n_score:.0f}/100</b>  |  Conf: <b>{n_conf:.0f}%</b>
{bias_emoji} Bias: <b>{n_bias}</b>  |  Força: <b>{n_bstr:.0f}%</b>  |  Edge: <b>{n_edge:+.1f}pts</b>
🎯 Concordância: <b>{n_agree:.0f}%</b>  |  Modelo dom.: <b>{n_dom}</b>{balance_note}
📊 Bull: <b>{n_bull:.0f}%</b> {_pbar(n_bull)}
📉 Bear: <b>{n_bear:.0f}%</b> {_pbar(n_bear)}
↔️  Side: <b>{n_side:.0f}%</b> {_pbar(n_side)}
💥 VolExp: <b>{n_vexp:.0f}%</b>  |  🎭 Manip: <b>{n_manip:.0f}%</b>{mconf_str}{rare_neural_line}"""

        if n_acc > 0:
            neural_section += f"\n📈 Accuracy recente: <b>{n_acc:.0f}%</b>"
        if n_calib and n_calib not in ("calibrado", ""):
            neural_section += f"\n⚙️ Calib: <i>{n_calib}</i>"

        msg = msg.rstrip() + neural_section

    msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ <i>Apenas análise — não é recomendação de investimento.</i>"

    return send_message(msg.strip())


def send_trinity_signal(
    price: float,
    trinity_result: dict,
    symbol: str = None,
) -> bool:
    """
    Envia o sinal do Trinity Core (Cap. 18) ao Telegram.

    Só deve ser chamado quando alert_priority in (CRITICAL, HIGH) ou
    quando trade_allowed == True.

    Args:
        price:          preço atual do ativo
        trinity_result: saída completa de run_trinity_core()
        symbol:         símbolo (padrão: SYMBOL do config)
    """
    sym = symbol or SYMBOL
    now = datetime.now().strftime("%d/%m %H:%M")

    # ── Campos do sinal ───────────────────────────────────────────────────────
    signal          = trinity_result.get("signal",               "NEUTRAL")
    strength        = trinity_result.get("signal_strength",      "NEUTRAL")
    confidence      = trinity_result.get("confidence",           0.0)
    trinity_score   = trinity_result.get("trinity_score",        50.0)
    risk_level      = trinity_result.get("risk_level",           "HIGH")
    market_bias     = trinity_result.get("market_bias",          "NEUTRAL")
    trade_allowed   = trinity_result.get("trade_allowed",        False)
    pos_factor      = trinity_result.get("position_size_factor", 0.0)
    rationale       = trinity_result.get("signal_rationale",     "N/A")
    alert_priority  = trinity_result.get("alert_priority",       "LOW")
    consensus       = trinity_result.get("consensus",            "NEUTRAL")
    conflict        = trinity_result.get("conflict",             False)
    conflict_level  = trinity_result.get("conflict_level",       "HIGH")
    engine_align    = trinity_result.get("engine_alignment",     50.0)
    market_regime   = trinity_result.get("market_regime",        "RANGING")
    pipeline_ms     = trinity_result.get("pipeline_ms",          0.0)

    # ── Sub-dicts ─────────────────────────────────────────────────────────────
    consensus_data  = trinity_result.get("consensus_data",  {})
    conflict_data   = trinity_result.get("conflict_data",   {})
    scoring_data    = trinity_result.get("scoring",         {})
    confidence_data = trinity_result.get("confidence_data", {})
    engine_results  = trinity_result.get("engine_results",  {})

    bull_n    = consensus_data.get("bullish_count", 0)
    bear_n    = consensus_data.get("bearish_count", 0)
    neut_n    = consensus_data.get("neutral_count", 0)
    top_contr = scoring_data.get("top_contributors", [])
    regime_mod = scoring_data.get("regime_modifier", 1.0)

    # ── Emojis ────────────────────────────────────────────────────────────────
    if signal == "LONG":
        sig_emoji  = "🟢📈"
        sig_label  = "LONG"
    elif signal == "SHORT":
        sig_emoji  = "🔴📉"
        sig_label  = "SHORT"
    else:
        sig_emoji  = "⚪️"
        sig_label  = "NEUTRAL"

    strength_map = {
        "STRONG":   "💪💪 STRONG",
        "MODERATE": "📊 MODERATE",
        "WEAK":     "🟡 WEAK",
        "NEUTRAL":  "⚪️ NEUTRAL",
    }
    strength_label = strength_map.get(strength, strength)

    risk_map = {"LOW": "🟢 LOW", "MEDIUM": "🟡 MEDIUM", "HIGH": "🔴 HIGH"}
    risk_label = risk_map.get(risk_level, risk_level)

    conflict_map = {"LOW": "✅ LOW", "MEDIUM": "⚠️ MEDIUM", "HIGH": "🚨 HIGH"}
    conflict_label = conflict_map.get(conflict_level, conflict_level)

    priority_map = {
        "CRITICAL": "🔴 CRITICAL",
        "HIGH":     "🟠 HIGH",
        "MEDIUM":   "🟡 MEDIUM",
        "LOW":      "⚪️ LOW",
    }
    priority_label = priority_map.get(alert_priority, alert_priority)

    regime_emoji = {
        "TRENDING":             "📈",
        "RANGING":              "↔️",
        "REVERSAL":             "🔄",
        "ACCUMULATION":         "🟡",
        "DISTRIBUTION":         "🟠",
        "VOLATILITY_EXPANSION": "💥",
        "MANIPULATION":         "🎭",
    }.get(market_regime, "❓")

    trade_line = (
        f"✅ Trade liberado  |  Tamanho: <b>{pos_factor*100:.0f}%</b>"
        if trade_allowed else
        "❌ Trade bloqueado (conflito ou sinal insuficiente)"
    )

    # ── Top engines contribuidores ────────────────────────────────────────────
    top_engines_str = "N/A"
    if top_contr:
        top_engines_str = "  |  ".join(
            f"{e}(<b>{v:.2f}</b>)" for e, v in top_contr[:3]
        )

    # ── Detalhes de conflito (se houver pares críticos) ────────────────────────
    spec_conflicts = conflict_data.get("specific_conflicts", [])
    conflict_detail = ""
    if spec_conflicts:
        pairs = [c["pair"] for c in spec_conflicts[:2]]
        conflict_detail = f"\n⚠️ Pares críticos: <i>{', '.join(pairs)}</i>"

    # ── Detalhes de confiança ─────────────────────────────────────────────────
    qb = confidence_data.get("quality_breakdown", {})
    degrad = confidence_data.get("degradation_reasons", [])
    quality_str = ""
    if qb:
        quality_str = (
            f"\n🔬 Acordo: <b>{qb.get('agreement',0):.0f}</b>  "
            f"Força: <b>{qb.get('strength',0):.0f}</b>  "
            f"HW: <b>{qb.get('hw_align',0):.0f}</b>  "
            f"EngConf: <b>{qb.get('engine_conf',0):.0f}</b>"
        )

    degrad_str = ""
    if degrad:
        degrad_str = f"\n📉 Penalidades: <i>{' | '.join(degrad[:2])}</i>"

    # ── Alinhamento dos engines (compacto) ────────────────────────────────────
    engines_line = ""
    if engine_results:
        bull_eng = [k for k, v in engine_results.items() if v.get("direction") == "BULLISH"]
        bear_eng = [k for k, v in engine_results.items() if v.get("direction") == "BEARISH"]
        engines_line = (
            f"\n🟢 Bull engines ({len(bull_eng)}): <i>{', '.join(bull_eng[:4])}</i>"
            f"\n🔴 Bear engines ({len(bear_eng)}): <i>{', '.join(bear_eng[:4])}</i>"
        )

    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{sig_emoji} <b>TRINITY CORE</b> — {sym}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 TF: {TIMEFRAME}  |  🕐 {now}\n"
        f"💵 Preço: <b>${price:,.2f}</b>\n"
        f"🚨 Prioridade: <b>{priority_label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>🎯 SINAL FINAL:</b>\n"
        f"{sig_emoji} <b>{sig_label}</b>  |  {strength_label}\n"
        f"💯 Confiança: <b>{confidence:.0f}%</b>\n"
        f"🏆 Trinity Score: <b>{trinity_score:.1f}/100</b>\n"
        f"📐 Market Bias: <b>{market_bias}</b>\n"
        f"{risk_label} Risco: <b>{risk_level}</b>\n"
        f"{trade_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>🗳️ CONSENSO DOS ENGINES:</b>\n"
        f"📊 {consensus}  |  Alinhamento: <b>{engine_align:.0f}%</b>\n"
        f"🟢 Bullish: <b>{bull_n}</b>  🔴 Bearish: <b>{bear_n}</b>  ⚪️ Neutro: <b>{neut_n}</b>\n"
        f"{engines_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>⚡ CONFLITO & REGIME:</b>\n"
        f"Conflito: {conflict_label}{conflict_detail}\n"
        f"Regime: {regime_emoji} <b>{market_regime.replace('_', ' ')}</b>  |  "
        f"Mod: <b>{regime_mod:.2f}x</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>🔬 QUALIDADE DO SINAL:</b>\n"
        f"Quality: <b>{confidence_data.get('signal_quality','LOW')}</b>  |  "
        f"Tradeable: {'✅' if confidence_data.get('is_tradeable') else '❌'}"
        f"{quality_str}"
        f"{degrad_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>🏅 TOP CONTRIBUIDORES:</b>\n"
        f"{top_engines_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📝 RATIONALE:</b>\n"
        f"<i>{rationale}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>⏱ Pipeline: {pipeline_ms:.0f}ms  |  ⚠️ Apenas análise.</i>"
    )

    return send_message(msg.strip())


def send_status_update(
    price: float,
    trinity_score: float,
    neural_data: dict,
    inst_data: dict,
    pressure_data: dict,
    direction_data: dict,
    blocked_reason: str,
    struct: str = "N/A",
) -> None:
    """
    Envia atualização periódica de status ao Telegram mesmo sem sinal de trade.
    Respeitado cooldown de STATUS_COOLDOWN_H horas, exceto se houver rare neural setup ativo.
    """
    global _last_status_ts
    now_ts    = time.time()
    nd        = neural_data or {}
    has_rare  = nd.get("rare_neural_setup", False)
    in_cooldown = (now_ts - _last_status_ts) < STATUS_COOLDOWN_H * 3600
    if in_cooldown and not has_rare:
        return  # silencia dentro do cooldown (sem rare setup)
    _last_status_ts = now_ts

    try:
        now = datetime.now().strftime("%d/%m %H:%M")

        # Neural
        nd       = neural_data or {}
        n_bias   = nd.get("neural_bias",                    "NEUTRAL")
        n_score  = nd.get("neural_score",                    50.0)
        n_regime = nd.get("market_regime",                  "RANGING")
        n_conf   = nd.get("confidence_score",                0.0)
        n_bull   = nd.get("bull_probability",                20.0)
        n_bear   = nd.get("bear_probability",                20.0)
        n_side   = nd.get("sideways_probability",            35.0)
        n_rare   = nd.get("rare_neural_setup",               False)
        n_rtype  = nd.get("rare_neural_type",                None)

        # Institucional
        confl    = inst_data.get("confluences",  0)
        i_score  = inst_data.get("inst_score",   0.0)
        i_dir    = inst_data.get("direction",    "N/A")

        # Pressão / Direction
        pressure  = pressure_data.get("pressure", 0.0)  if pressure_data  else 0.0
        dir_score = direction_data.get("direction_score", 50.0) if direction_data else 50.0
        dir_dir   = direction_data.get("direction",       "N/A") if direction_data else "N/A"

        # Motivo do bloqueio
        reason_map = {
            "confluencias": f"Confluências insuficientes ({confl}/6)",
            "lateral":      f"Mercado lateral/indefinido ({struct})",
            "score_baixo":  f"Trinity Score abaixo do limiar ({trinity_score:.0f})",
        }
        reason_txt = reason_map.get(blocked_reason, blocked_reason)

        bias_emoji = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪️"}.get(n_bias, "⚪️")
        regime_emoji = {
            "TRENDING": "📈", "RANGING": "↔️", "REVERSAL": "🔄",
            "ACCUMULATION": "🟡", "DISTRIBUTION": "🟠",
            "VOLATILITY_EXPANSION": "💥", "MANIPULATION": "🎭",
        }.get(n_regime, "❓")

        rare_line = ""
        if n_rare and n_rtype:
            rare_emojis = {
                "ACCUMULATION_WHALE_BUYING": "🐋",
                "DISTRIBUTION_ABSORPTION":   "🕳️",
                "COMPRESSION_VOL_EXPANSION": "💥",
            }
            r_emoji   = rare_emojis.get(n_rtype, "★")
            rare_line = f"\n{r_emoji} <b>RARE NEURAL: {n_rtype.replace('_', ' ')}</b>"

        msg = (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 <b>STATUS — {SYMBOL}</b>  |  🕐 {now}  |  ⏱ {TIMEFRAME}\n"
            f"💵 Preço: <b>${price:,.2f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💤 <b>Sem sinal:</b> {reason_txt}\n"
            f"🏛 Institucional: score <b>{i_score:.0f}</b>  |  {confl}/6 conf  |  Dir: <b>{i_dir}</b>\n"
            f"⚡ Pressão: <b>{pressure:+.1f}</b>  |  Trinity: <b>{trinity_score:.0f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>🧠 Neural:</b>  {regime_emoji} {n_regime.replace('_', ' ')}\n"
            f"{bias_emoji} Bias: <b>{n_bias}</b>  |  Score: <b>{n_score:.0f}</b>  |  Conf: <b>{n_conf:.0f}%</b>\n"
            f"📊 Bull: <b>{n_bull:.0f}%</b>  Bear: <b>{n_bear:.0f}%</b>  Side: <b>{n_side:.0f}%</b>\n"
            f"📊 Direction: <b>{dir_dir}</b>  |  Score: <b>{dir_score:.0f}</b>{rare_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>⏰ Próxima análise em ~{INST_INTERVAL_MINUTES} min</i>"
        )

        send_message(msg.strip())
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(f"[alerts] send_status_update falhou: {exc}")
