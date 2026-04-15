"""
analysis_engine.py — Gera análise macro institucional via Claude API.

Recebe os dados coletados pelo data_collector e usa a API do Claude
(claude-sonnet-4-20250514) para produzir análise de nível hedge fund.

O output é texto estruturado que será renderizado em PDF.
"""
import logging
import requests

log = logging.getLogger(__name__)


def _get_anthropic_key() -> str:
    import os
    # Prioridade: env var (Render) → config.py
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if key:
        return key
    try:
        import sys
        from pathlib import Path
        base = Path(__file__).parent.parent.parent
        if str(base) not in sys.path:
            sys.path.insert(0, str(base))
        from config import ANTHROPIC_API_KEY
        return ANTHROPIC_API_KEY or ""
    except Exception:
        return ""


def generate_analysis(collected_data: dict) -> dict:
    """
    Gera análise macro usando Claude API.

    Args:
        collected_data: output do data_collector.collect_all()

    Returns:
        {
            "executive_summary": str,
            "regime_macro": str,
            "mercado_crypto": str,
            "ciclo_position": str,
            "cenarios": str,
            "veredito": str,
            "raw_response": str,
        }
    """
    api_key = _get_anthropic_key()
    if not api_key:
        log.error("[AnalysisEngine] Anthropic API key não configurada")
        return _fallback_analysis(collected_data)

    # ── Formatar dados para injeção ───────────────────────────────────────
    data_context = _format_data_context(collected_data)

    # ── System prompt ─────────────────────────────────────────────────────
    system_prompt = """Você é um estrategista macro sênior de um fundo sistemático crypto com $2B+ AUM. Você produz o relatório semanal interno que orienta as decisões de alocação do fundo.

Seu relatório deve ser:
- FACTUAL: Cada afirmação deve ser ancorada em dados verificáveis. Se um dado não está disponível, declare "dado indisponível".
- PROBABILÍSTICO: Nunca fale em certezas. Use probabilidades e intervalos.
- CONTRARIAN-AWARE: Para cada tese, identifique o maior risco de estar errado.
- ACIONÁVEL: Cada seção termina com implicação prática.

REGRAS:
1. Não repita informação entre seções.
2. Diferencie DADOS (o que aconteceu) de INTERPRETAÇÃO (o que significa).
3. Priorize sinais que DIVERGEM do consenso.
4. Use "Se X, então Y, a menos que Z" para projeções.
5. Responda em PORTUGUÊS BRASILEIRO.
6. Use formato limpo com marcadores claros para cada seção."""

    # ── User prompt ───────────────────────────────────────────────────────
    user_prompt = f"""DADOS ATUALIZADOS (coletados pelo Trinity Trading Bot):

{data_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Com base nestes dados, produza o RELATÓRIO MACRO SEMANAL INSTITUCIONAL — Bitcoin & Crypto.

Estruture em EXATAMENTE 6 seções, cada uma com o delimitador [SECTION_N] no início:

[SECTION_1] EXECUTIVE SUMMARY
3-4 parágrafos densos. Veredito em uma frase no final. Probabilidades para os próximos 30 dias.

[SECTION_2] REGIME MACRO GLOBAL
Analise: Liquidez (M2, NFCI, Fed Balance Sheet), Yields (10Y, 2Y, spread), VIX, Credit spreads.
Classifique: RISK-ON / RISK-OFF / TRANSIÇÃO.
Termine com: vetor dominante e catalisador de mudança.

[SECTION_3] MERCADO CRYPTO & DERIVATIVOS
Analise: BTC price action, funding rates, L/S ratio, OI, funding extremos em altcoins.
Foque em: posicionamento (quem está overexposed), risco de liquidation cascade.
Termine com: smart money acumulando ou distribuindo?

[SECTION_4] POSIÇÃO NO CICLO
Use 3 frameworks: (1) Halving (abril 2024, ~365 dias atrás), (2) Liquidez (M2 defasado 10-12 semanas), (3) Bull Market Support Band (20W SMA / 21W EMA).
Compare com 2022: padrão de canal descendente tocando teto.
Termine com: qual momento histórico mais se parece com agora?

[SECTION_5] CENÁRIOS PROBABILÍSTICOS
Construa EXATAMENTE 4 cenários. Os 4 devem somar 100%.
Para cada: Nome | Probabilidade | Tese | Catalisadores | Kill shot | Range BTC 30 dias | Range BTC 90 dias | Impacto alts | Posicionamento.

[SECTION_6] VEREDITO FINAL
Dashboard: Liquidez (25%), Política Monetária (15%), DXY/Yields (10%), Derivativos (15%), Estrutura Técnica (15%), Posição no Ciclo (10%), Sazonalidade (10%).
Classifique cada como BULLISH / NEUTRO / BEARISH.
Score ponderado 0-100.
Viés direcional + convicção.
Projeções condicionais (Se A -> B, Se C -> D).
Níveis-chave (resistências e suportes).
O que mudaria a tese."""

    # ── Chamar Claude API ─────────────────────────────────────────────────
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 8000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=120,
        )

        if response.status_code != 200:
            log.error(f"[AnalysisEngine] Claude API error: {response.status_code} — {response.text[:200]}")
            return _fallback_analysis(collected_data)

        result = response.json()
        raw_text = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                raw_text += block["text"]

        log.info(f"[AnalysisEngine] Análise gerada — {len(raw_text)} chars")

        # ── Parsear seções ────────────────────────────────────────────────
        sections = _parse_sections(raw_text)
        sections["raw_response"] = raw_text

        return sections

    except Exception as e:
        log.error(f"[AnalysisEngine] Erro: {e}")
        return _fallback_analysis(collected_data)


def _format_data_context(data: dict) -> str:
    """Formata os dados coletados em texto legível para injeção no prompt."""
    lines = []

    # BTC Market
    btc = data.get("btc_market", {})
    if btc:
        lines.append("=== BTC MARKET ===")
        lines.append(f"Preco: ${btc.get('price', 0):,.2f}")
        lines.append(f"Variacao 24h: {btc.get('change_24h_pct', 0):+.2f}%")
        lines.append(f"Range 24h: ${btc.get('low_24h', 0):,.0f} -- ${btc.get('high_24h', 0):,.0f}")
        lines.append(f"Volume 24h: ${btc.get('volume_24h_usd', 0)/1e6:,.1f}M")
        lines.append(f"Funding Rate: {btc.get('funding_rate', 0)*100:+.4f}%/8h ({btc.get('funding_ann', 0):+.1f}%/yr)")
        lines.append(f"Open Interest: ${btc.get('oi_usd', 0)/1e9:,.2f}B")
        if btc.get("eth_price"):
            lines.append(f"ETH: ${btc['eth_price']:,.2f} ({btc.get('eth_change_pct', 0):+.1f}%)")

    # Macro FRED
    macro = data.get("macro_fred", {})
    if macro:
        lines.append("\n=== MACRO (FRED) ===")
        if macro.get("nfci_current") is not None:
            lines.append(f"NFCI: {macro['nfci_current']:.3f} (prev: {macro.get('nfci_prev_week', '?')}) -- {macro.get('nfci_direction', '?')}")
            if macro.get("nfci_4w_ago"):
                lines.append(f"NFCI 4 semanas atras: {macro['nfci_4w_ago']:.3f}")
            if macro.get("nfci_12w_ago"):
                lines.append(f"NFCI 12 semanas atras: {macro['nfci_12w_ago']:.3f}")
        if macro.get("m2_current"):
            lines.append(f"M2 Money Supply: ${macro['m2_current']:,.1f}B (YoY: {macro.get('m2_yoy_pct', 0):+.2f}%, MoM: {macro.get('m2_mom_pct', 0):+.2f}%)")
            lines.append(f"M2 direcao: {macro.get('m2_direction', '?')}")
        if macro.get("fed_balance_sheet"):
            lines.append(f"Fed Balance Sheet: ${macro['fed_balance_sheet']:.2f}T (var 4w: {macro.get('fed_bs_change_4w', 0):+.3f}T)")
        if macro.get("treasury_10y"):
            lines.append(f"Treasury 10Y: {macro['treasury_10y']:.2f}%")
        if macro.get("treasury_2y"):
            lines.append(f"Treasury 2Y: {macro['treasury_2y']:.2f}%")
        if macro.get("yield_spread_10y_2y") is not None:
            lines.append(f"Spread 10Y-2Y: {macro['yield_spread_10y_2y']:.3f}%")
        if macro.get("vix"):
            lines.append(f"VIX: {macro['vix']:.2f}")
        if macro.get("hy_spread"):
            lines.append(f"HY Credit Spread: {macro['hy_spread']:.2f}%")

    # Trinity Regime
    regime = data.get("regime_trinity", {})
    if regime:
        lines.append("\n=== TRINITY REGIME ENGINE ===")
        lines.append(f"Regime: {regime.get('direction', '?')} | Bias: {regime.get('bias', '?')} | Strength: {regime.get('strength', 0)}")
        lines.append(f"Bull score: {regime.get('bull_score', 0)} | Bear score: {regime.get('bear_score', 0)} | Confirmacoes: {regime.get('confirmations', 0)}")
        lines.append(f"Bull Support Band: SMA20w=${regime.get('bull_band_sma', 0):,.0f} | EMA21w=${regime.get('bull_band_ema', 0):,.0f}")
        lines.append(f"Bull Band bias: {regime.get('bull_band_bias', '?')} | Distancia: {regime.get('bull_band_dist', 0):+.1f}%")
        lines.append(f"NFCI: {regime.get('nfci_value', 0):.3f} ({regime.get('nfci_regime', '?')})")
        lines.append(f"M2 YoY: {regime.get('m2_yoy_pct', 0):+.1f}% ({regime.get('m2_direction', '?')})")
        lines.append(f"Sazonalidade: Mes {regime.get('seasonality_month', 0)} x{regime.get('seasonality_mult', 1):.2f} ({regime.get('seasonality_season', '?')})")
        if regime.get("transition"):
            lines.append(f"TRANSICAO ATIVA: {regime['transition']}")
        if regime.get("signals"):
            lines.append(f"Sinais ({len(regime['signals'])}):")
            for sig in regime["signals"][:8]:
                lines.append(f"  - {sig}")

    # Derivatives
    deriv = data.get("derivatives", {})
    if deriv:
        lines.append("\n=== DERIVATIVOS ===")
        lines.append(f"BTC L/S ratio: {deriv.get('btc_ls_ratio', '?')} (Long: {deriv.get('btc_long_pct', '?')}% / Short: {deriv.get('btc_short_pct', '?')}%)")

    # Funding Extremes
    funding = data.get("funding_extreme", {})
    if funding and funding.get("top"):
        lines.append(f"\n=== FUNDING EXTREMOS ({funding.get('count', 0)} coins) ===")
        for f in funding["top"][:8]:
            lines.append(f"  {f['symbol']:12} {f['rate']:+.4f}%/8h ({f['ann']:+,.0f}%/yr) -> {f['direction']}")

    # Top Movers
    movers = data.get("top_movers", {})
    if movers:
        lines.append("\n=== TOP MOVERS 24H ===")
        lines.append("Gainers:")
        for g in movers.get("gainers", [])[:5]:
            lines.append(f"  {g['symbol']:12} {g['pct']:+.1f}%  vol=${g['vol']/1e6:.1f}M")
        lines.append("Losers:")
        for lsr in movers.get("losers", [])[:5]:
            lines.append(f"  {lsr['symbol']:12} {lsr['pct']:+.1f}%  vol=${lsr['vol']/1e6:.1f}M")

    # Alt Market
    alt = data.get("alt_market", {})
    if alt:
        lines.append(f"\n=== MERCADO ALTCOINS ===")
        lines.append(f"Total contratos MEXC: {alt.get('total_contracts', 0)}")
        lines.append(f"Positivos: {alt.get('positive_pct', 0)}% | Negativos: {alt.get('negative_pct', 0)}%")
        lines.append(f"Variacao media: {alt.get('avg_change_pct', 0):+.2f}%")

    return "\n".join(lines)


def _parse_sections(text: str) -> dict:
    """Parseia o texto do Claude em seções."""
    sections = {
        "executive_summary": "",
        "regime_macro": "",
        "mercado_crypto": "",
        "ciclo_position": "",
        "cenarios": "",
        "veredito": "",
    }

    section_map = {
        "[SECTION_1]": "executive_summary",
        "[SECTION_2]": "regime_macro",
        "[SECTION_3]": "mercado_crypto",
        "[SECTION_4]": "ciclo_position",
        "[SECTION_5]": "cenarios",
        "[SECTION_6]": "veredito",
    }

    current_section = None
    current_text = []

    for line in text.split("\n"):
        matched = False
        for marker, key in section_map.items():
            if marker in line:
                # Salvar seção anterior
                if current_section:
                    sections[current_section] = "\n".join(current_text).strip()
                current_section = key
                current_text = []
                # Adicionar texto após o marcador
                remainder = line.split(marker, 1)[1].strip()
                if remainder:
                    current_text.append(remainder)
                matched = True
                break
        if not matched and current_section:
            current_text.append(line)

    # Última seção
    if current_section:
        sections[current_section] = "\n".join(current_text).strip()

    # Se nenhuma seção parseada, colocar tudo no executive_summary
    if not any(sections.values()):
        sections["executive_summary"] = text

    return sections


def _fallback_analysis(data: dict) -> dict:
    """Fallback quando Claude API não está disponível."""
    btc    = data.get("btc_market", {})
    regime = data.get("regime_trinity", {})
    macro  = data.get("macro_fred", {})

    price   = btc.get("price", 0)
    bb_dist = regime.get("bull_band_dist", 0)
    nfci    = macro.get("nfci_current", 0) or 0

    return {
        "executive_summary": (
            f"BTC opera em ${price:,.0f} com regime {regime.get('direction', 'NEUTRAL')} "
            f"(strength {regime.get('strength', 0)}). "
            f"Bull Support Band indica bias {regime.get('bull_band_bias', '?')} "
            f"({bb_dist:+.1f}% do band). "
            f"NFCI em {nfci:.3f} ({regime.get('nfci_regime', '?')}). "
            f"M2 {macro.get('m2_yoy_pct', 0):+.1f}% YoY ({macro.get('m2_direction', '?')}). "
            f"Analise detalhada indisponivel — Claude API nao configurada."
        ),
        "regime_macro":    "Analise requer Claude API key.",
        "mercado_crypto":  "Analise requer Claude API key.",
        "ciclo_position":  "Analise requer Claude API key.",
        "cenarios":        "Analise requer Claude API key.",
        "veredito":        "Configure ANTHROPIC_API_KEY para relatorio completo.",
        "raw_response":    "",
    }
