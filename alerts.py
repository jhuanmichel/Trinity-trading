"""
alerts.py — Sistema de Alertas via Telegram
Envia sinais formatados e profissionais para o seu chat.
"""
import asyncio
import requests
import json
from datetime import datetime
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, SYMBOL, TIMEFRAME, SCORE_THRESHOLD


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
    """Envia resumo periódico mesmo sem sinal (para monitoramento)."""
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
    mm_data:       dict = None,
    pressure_data: dict = None,
    rare_data:     dict = None,
    trinity_score: float = None,
    geo_data:      dict = None,
    cycle_data:    dict = None,
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
🔗 Confluências: <b>{confluences}/6 camadas</b>
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

    msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ <i>Apenas análise — não é recomendação de investimento.</i>"

    return send_message(msg.strip())
