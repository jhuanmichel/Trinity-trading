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
    mm_data: dict = None,
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

        msg = msg.rstrip() + mm_section + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ <i>Apenas análise — não é recomendação de investimento.</i>"

    return send_message(msg.strip())
