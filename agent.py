"""
agent.py — CÉREBRO DA IA
Monta o contexto completo de todos os módulos e pede análise ao Claude.
Retorna análise estruturada com entrada, stop, alvo e justificativa.
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY, SYMBOL, TIMEFRAME


client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


SYSTEM_PROMPT = """Você é um analista quantitativo sênior especializado em futuros perpétuos de criptomoedas.
Você analisa dados técnicos, de derivativos, on-chain e sentimento para gerar análises precisas.

Suas análises devem ser:
- Objetivas e baseadas nos dados fornecidos
- Conscientes de que Bitcoin passa ~70% do tempo em range
- Conservadoras em situações de mercado lateral
- Atentas a divergências entre indicadores

Sempre retorne SOMENTE um objeto JSON válido, sem texto adicional, sem markdown."""


def build_prompt(price: float, analyses: dict, score_data: dict) -> str:
    """Monta o prompt com todos os dados para o Claude analisar."""

    regime  = analyses.get("regime", {})
    trend   = analyses.get("trend", {})
    mom     = analyses.get("momentum", {})
    vol     = analyses.get("volume", {})
    deriv   = analyses.get("derivatives", {})
    liq     = analyses.get("liquidations", {})
    onchain = analyses.get("onchain", {})
    sent    = analyses.get("sentiment", {})

    return f"""Analise os dados abaixo e retorne um JSON com sua análise completa.

═══════════════════════════════════════
PAR: {SYMBOL} | TIMEFRAME: {TIMEFRAME}
PREÇO ATUAL: ${price:,.2f}
═══════════════════════════════════════

1. REGIME DE MERCADO:
   {regime.get('summary', 'N/A')}

2. TENDÊNCIA:
   {trend.get('summary', 'N/A')}

3. MOMENTUM:
   {mom.get('summary', 'N/A')}

4. VOLUME:
   {vol.get('summary', 'N/A')}

5. DERIVATIVOS:
   {deriv.get('summary', 'N/A')}

5b. LIQUIDAÇÕES E DADOS DERIVADOS:
   {liq.get('summary', 'N/A')}
   Liquidações 24h: Longs ${liq.get('liq_24h_long_usd', 0):.1f}M / Shorts ${liq.get('liq_24h_short_usd', 0):.1f}M
   Taker Buy/Sell: {liq.get('taker_ratio', 'N/A')} ({liq.get('taker_signal', 'N/A')})
   Alavancagem estimada: {liq.get('leverage_ratio', 'N/A')} ({liq.get('leverage_signal', 'N/A')})
   Mineradores: {liq.get('miner_signal', 'N/A')}
   {liq.get('heatmap_summary', '')}

6. ON-CHAIN:
   {onchain.get('summary', 'N/A')}

7. SENTIMENTO:
   {sent.get('summary', 'N/A')}

8. SCORE PROBABILÍSTICO:
   LONG: {score_data.get('prob_long', 50):.1f}% | SHORT: {score_data.get('prob_short', 50):.1f}%
   Sinal sugerido pelo modelo: {score_data.get('signal', 'N/A')} (confiança: {score_data.get('confidence', 'N/A')})

═══════════════════════════════════════

Com base nesses dados, retorne EXATAMENTE este JSON:
{{
  "direcao": "LONG" | "SHORT" | "NO_TRADE",
  "forca_sinal": <número de 1 a 10>,
  "entrada_ideal": <preço sugerido para entrada>,
  "stop_loss": <preço de stop loss>,
  "take_profit_1": <primeiro alvo>,
  "take_profit_2": <segundo alvo>,
  "risco_retorno": <razão risco/retorno, ex: "1:3">,
  "risco_nivel": "BAIXO" | "MÉDIO" | "ALTO",
  "regime_atual": <descrição do regime em 1 frase>,
  "justificativa": <análise em 3-4 frases explicando o sinal>,
  "alertas": [<lista de alertas importantes, ex: divergências, contradições entre indicadores>],
  "melhor_cenario": <o que confirmaria o sinal>,
  "pior_cenario": <o que invalidaria o sinal>
}}"""


def analyze(price: float, analyses: dict, score_data: dict) -> dict:
    """
    Envia os dados para o Claude e retorna a análise estruturada.

    Returns:
        dict com a análise completa do Claude
    """
    prompt = build_prompt(price, analyses, score_data)

    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = message.content[0].text.strip()

        # Remove possível markdown de código
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        raw_text = raw_text.strip()

        result = json.loads(raw_text)
        result["_raw_prompt_tokens"]     = message.usage.input_tokens
        result["_raw_response_tokens"]   = message.usage.output_tokens
        return result

    except json.JSONDecodeError as e:
        return {"error": f"JSON inválido: {e}", "direcao": "NO_TRADE", "forca_sinal": 0}
    except Exception as e:
        return {"error": str(e), "direcao": "NO_TRADE", "forca_sinal": 0}
