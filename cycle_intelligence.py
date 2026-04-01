"""
cycle_intelligence.py — Módulo de Ciclos de Mercado do Bitcoin
Analisa ciclos históricos de 4 anos, fase atual e projeta cenários de preço.
Entregue no Morning Brief semanal (toda segunda-feira).
"""

import os
import json
import requests
import anthropic
from datetime import datetime
from io import BytesIO
from typing import Optional
from dotenv import load_dotenv

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, Image as RLImage
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY

load_dotenv()

IMG_MAX_WIDTH_CM = 17
IMG_MAX_HEIGHT_CM = 11

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Cores ────────────────────────────────────────────────────────────────────
ACCENT  = HexColor("#F7931A")
GREEN   = HexColor("#2ECC71")
RED     = HexColor("#E74C3C")
BLUE    = HexColor("#3498DB")
YELLOW  = HexColor("#F1C40F")
PURPLE  = HexColor("#9B59B6")
GRAY    = HexColor("#8B949E")
DARK    = HexColor("#343A40")
LIGHT   = HexColor("#F8F9FA")
BORDER  = HexColor("#DEE2E6")


# ─── Fases do ciclo ───────────────────────────────────────────────────────────
CYCLE_PHASES = {
    "ACUMULAÇÃO":   {"color": BLUE,   "emoji": "🔵"},
    "EXPANSÃO":     {"color": GREEN,  "emoji": "🟢"},
    "EUFORIA":      {"color": YELLOW, "emoji": "🟡"},
    "DISTRIBUIÇÃO": {"color": PURPLE, "emoji": "🟣"},
    "BEAR MARKET":  {"color": RED,   "emoji": "🔴"},
    "RECUPERAÇÃO":  {"color": BLUE,   "emoji": "🔵"},
}


def _download_image(url: str) -> Optional[bytes]:
    if not url or not url.startswith("http"):
        return None
    try:
        r = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TrinityBot/1.0)"},
            stream=True,
        )
        r.raise_for_status()
        content = r.content
        if len(content) < 100 or len(content) > 5_000_000:
            return None
        return content
    except Exception:
        return None


def _make_reportlab_image(img_bytes: bytes, max_width_cm: float = IMG_MAX_WIDTH_CM, max_height_cm: float = IMG_MAX_HEIGHT_CM):
    try:
        from PIL import Image as PILImage
        stream = BytesIO(img_bytes)
        pil = PILImage.open(stream)
        w, h = pil.size
        if w <= 0 or h <= 0:
            return None
        scale_w = (max_width_cm * cm) / w
        scale_h = (max_height_cm * cm) / h
        scale = min(scale_w, scale_h, 1.0)
        nw, nh = w * scale, h * scale
        stream.seek(0)
        return RLImage(stream, width=nw, height=nh)
    except Exception:
        return None


def _add_curated_images(story, imagens: list, posicao: str, small_s):
    filtradas = [im for im in (imagens or []) if (im.get("posicao") or "").lower() == posicao]
    filtradas.sort(key=lambda x: x.get("ordem_importancia", 99))
    for item in filtradas:
        url = item.get("url")
        legenda_pt = item.get("legenda_pt") or item.get("funcao_analitica") or "Gráfico analítico."
        img_bytes = _download_image(url)
        if img_bytes:
            rl_img = _make_reportlab_image(img_bytes)
            if rl_img:
                story.append(Spacer(1, 8))
                story.append(rl_img)
                story.append(Spacer(1, 4))
                story.append(Paragraph(f"<i>{legenda_pt}</i>", small_s))
                story.append(Spacer(1, 6))
        else:
            story.append(Paragraph(f"<i>[Gráfico: {legenda_pt}]</i>", small_s))
            story.append(Spacer(1, 4))


def get_cycle_analysis() -> dict:
    """Usa Claude com web search para analisar o ciclo atual do Bitcoin."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today  = datetime.now().strftime("%d/%m/%Y")

    prompt = f"""Hoje é {today}. Você é um analista quantitativo sênior especializado em ciclos de mercado do Bitcoin.

Pesquise dados atuais do Bitcoin (preço, MVRV, dominância, fear & greed, on-chain) e faça uma análise completa de ciclo de mercado.

Em seguida, usando web search, selecione de 0 a 6 imagens analíticas relevantes (gráficos públicos de TradingView, Glassnode, CryptoQuant, Coinglass, The Block ou similares). Para cada imagem aplique o filtro: (1) relevante para o ciclo/mercado hoje? (2) melhora o entendimento? (3) acrescenta algo que o texto não entrega? (4) não repete outra? A mais importante primeiro (âncora visual). Em dias sem novidade visual relevante, use menos imagens — nunca force. Retorne URL direta da imagem (.png, .jpg, .jpeg ou .webp quando possível).

Retorne APENAS um JSON válido com esta estrutura:

{{
  "preco_atual": "$XX,XXX",

  "educacao": {{
    "o_que_e_bull_market": "Explicação simples em 2-3 frases para leigos",
    "o_que_e_bear_market": "Explicação simples em 2-3 frases para leigos",
    "o_que_e_halving": "Explicação simples em 2-3 frases para leigos"
  }},

  "ciclo": {{
    "fase_atual": "ACUMULAÇÃO" | "EXPANSÃO" | "EUFORIA" | "DISTRIBUIÇÃO" | "BEAR MARKET" | "RECUPERAÇÃO",
    "confianca_pct": <0-100>,
    "dias_desde_ultimo_halving": <número>,
    "data_proximo_halving": "MM/AAAA",
    "descricao_fase": "2-3 frases explicando por que estamos nessa fase"
  }},

  "status_mercado": {{
    "e_bullish": true | false,
    "classificacao": "BULLISH FORTE" | "BULLISH MODERADO" | "NEUTRO" | "BEARISH MODERADO" | "BEARISH FORTE",
    "estrutura": "descrição da estrutura de mercado em 1 frase",
    "prob_continuacao_alta_pct": <0-100>,
    "prob_distribuicao_pct": <0-100>,
    "resposta_direta": "Resposta direta à pergunta: estamos realmente em bullish? 2-3 frases"
  }},

  "confirmacoes_tecnicas": [
    {{"indicador": "nome", "leitura": "valor atual", "interpretacao": "bullish/bearish/neutro", "detalhe": "1 frase"}}
  ],

  "cenarios_preco": {{
    "horizonte": "próximas 4-12 semanas",
    "otimista":     {{"faixa": "$X a $Y", "probabilidade_pct": <0-100>, "condicao": "o que precisa acontecer"}},
    "base":         {{"faixa": "$X a $Y", "probabilidade_pct": <0-100>, "condicao": "o que precisa acontecer"}},
    "conservador":  {{"faixa": "$X a $Y", "probabilidade_pct": <0-100>, "condicao": "o que precisa acontecer"}}
  }},

  "topo_ciclo": {{
    "faixa_provavel": "$X a $Y",
    "janela_temporal": "ex: Q3-Q4 2025",
    "confianca_pct": <0-100>,
    "base_historica": "1-2 frases explicando a base histórica dessa projeção"
  }},

  "fundo_bear_market": {{
    "faixa_provavel": "$X a $Y",
    "queda_estimada_pct": "-XX% a -YY% a partir do topo",
    "janela_temporal": "ex: 2026-2027",
    "confianca_pct": <0-100>,
    "base_historica": "1-2 frases explicando a base histórica"
  }},

  "invalidacoes": [
    "sinal que invalidaria a tese atual — 1 por item"
  ],

  "imagens_curatorias": [
    {{
      "url": "URL direta da imagem (https://...)",
      "funcao_analitica": "o que a imagem mostra em 1 frase",
      "legenda_pt": "Legenda em português: o que é, o que mostra, por que importa hoje (2-3 frases)",
      "posicao": "inicio" | "tecnica" | "onchain" | "ciclo",
      "pergunta_que_responde": "qual pergunta do leitor esta imagem responde",
      "ordem_importancia": 1
    }}
  ],

  "resumo_humano": "Parágrafo final em linguagem simples e direta, resumindo tudo para um trader que quer saber: devo estar comprado, vendido ou neutro agora, e o que observar nas próximas semanas."
}}"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )

    full_text = ""
    for block in message.content:
        if hasattr(block, "text"):
            full_text += block.text

    try:
        clean = full_text.strip()
        if "```" in clean:
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        return json.loads(clean.strip())
    except Exception as e:
        print(f"Erro JSON: {e}")
        return _fallback_cycle()


def generate_cycle_pdf(data: dict) -> bytes:
    """Gera o PDF do Cycle Intelligence Report."""
    buffer = BytesIO()
    today  = datetime.now().strftime("%d de %B de %Y")
    today  = today[0].upper() + today[1:]

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=1.5*cm, leftMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title=f"Cycle Intelligence — {today}",
    )

    # ─── Estilos ──────────────────────────────────────────────────────────────
    title_s = ParagraphStyle("T", fontName="Helvetica-Bold", fontSize=22,
                              textColor=ACCENT, spaceAfter=4)
    sub_s   = ParagraphStyle("S", fontName="Helvetica", fontSize=10,
                              textColor=GRAY, spaceAfter=2)
    sec_s   = ParagraphStyle("Se", fontName="Helvetica-Bold", fontSize=13,
                              textColor=ACCENT, spaceBefore=14, spaceAfter=6)
    body_s  = ParagraphStyle("B", fontName="Helvetica", fontSize=9.5,
                              textColor=black, leading=14, spaceAfter=4)
    just_s  = ParagraphStyle("J", fontName="Helvetica", fontSize=9.5,
                              textColor=black, leading=14, spaceAfter=4,
                              alignment=TA_JUSTIFY)
    bold_s  = ParagraphStyle("Bo", fontName="Helvetica-Bold", fontSize=10,
                              textColor=black, spaceAfter=3)
    small_s = ParagraphStyle("Sm", fontName="Helvetica", fontSize=8.5,
                              textColor=GRAY, spaceAfter=2)
    center_s= ParagraphStyle("C", fontName="Helvetica-Bold", fontSize=11,
                              textColor=black, alignment=TA_CENTER)

    story = []

    # ─── Cabeçalho ────────────────────────────────────────────────────────────
    fase    = data.get("ciclo", {}).get("fase_atual", "?")
    conf    = data.get("ciclo", {}).get("confianca_pct", 50)
    preco   = data.get("preco_atual", "?")
    ph_info = CYCLE_PHASES.get(fase, {"color": GRAY, "emoji": "⚪"})
    fase_hex= ph_info["color"].hexval()[2:]

    hdr = [[
        Paragraph("<b>₿ CYCLE INTELLIGENCE</b>", title_s),
        Paragraph(
            f"<font color='#{fase_hex}'><b>{ph_info['emoji']} {fase}</b></font>  "
            f"<font color='#8B949E'>Confiança: {conf}%</font>",
            ParagraphStyle("HR", fontName="Helvetica-Bold", fontSize=12,
                           textColor=black, alignment=TA_RIGHT)
        ),
    ]]
    hdr_t = Table(hdr, colWidths=[10*cm, 8*cm])
    hdr_t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    story.append(hdr_t)
    story.append(Paragraph(
        f"{today}  •  Relatório Semanal de Ciclos  •  BTC: {preco}", sub_s))
    story.append(HRFlowable(width="100%", thickness=2, color=ACCENT, spaceAfter=8))
    _add_curated_images(story, data.get("imagens_curatorias", []), "inicio", small_s)

    # ─── Educação ─────────────────────────────────────────────────────────────
    edu = data.get("educacao", {})
    if edu:
        story.append(Paragraph("📚 ENTENDENDO O MERCADO — PARA TODOS", sec_s))
        for titulo, chave in [
            ("O que é Bull Market?", "o_que_e_bull_market"),
            ("O que é Bear Market?", "o_que_e_bear_market"),
            ("O que é Halving?",     "o_que_e_halving"),
        ]:
            texto = edu.get(chave, "")
            if texto:
                row = Table([
                    [Paragraph(f"<b>{titulo}</b>", bold_s)],
                    [Paragraph(texto, body_s)],
                ], colWidths=[17.5*cm])
                row.setStyle(TableStyle([
                    ("BACKGROUND",   (0,0),(-1,-1), LIGHT),
                    ("BOX",          (0,0),(-1,-1), 0.5, BORDER),
                    ("LEFTPADDING",  (0,0),(-1,-1), 8),
                    ("RIGHTPADDING", (0,0),(-1,-1), 8),
                    ("TOPPADDING",   (0,0),(0,0),   6),
                    ("BOTTOMPADDING",(0,-1),(-1,-1),6),
                ]))
                story.append(KeepTogether([row, Spacer(1, 5)]))

    # ─── Fase do ciclo ────────────────────────────────────────────────────────
    ciclo = data.get("ciclo", {})
    story.append(Paragraph("🔄 FASE ATUAL DO CICLO", sec_s))

    fase_data = [
        [
            Paragraph("Fase Atual", ParagraphStyle("FH", fontName="Helvetica-Bold",
                       fontSize=9, textColor=white, alignment=TA_CENTER)),
            Paragraph("Confiança", ParagraphStyle("FH", fontName="Helvetica-Bold",
                       fontSize=9, textColor=white, alignment=TA_CENTER)),
            Paragraph("Desde o Halving", ParagraphStyle("FH", fontName="Helvetica-Bold",
                       fontSize=9, textColor=white, alignment=TA_CENTER)),
            Paragraph("Próximo Halving", ParagraphStyle("FH", fontName="Helvetica-Bold",
                       fontSize=9, textColor=white, alignment=TA_CENTER)),
        ],
        [
            Paragraph(f"<font color='#{fase_hex}'><b>{ph_info['emoji']} {fase}</b></font>",
                      center_s),
            Paragraph(f"<b>{conf}%</b>", center_s),
            Paragraph(f"<b>{ciclo.get('dias_desde_ultimo_halving','?')} dias</b>", center_s),
            Paragraph(f"<b>{ciclo.get('data_proximo_halving','?')}</b>", center_s),
        ]
    ]
    fase_t = Table(fase_data, colWidths=[4.3*cm]*4)
    fase_t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,0), DARK),
        ("BACKGROUND",   (0,1),(-1,1), LIGHT),
        ("BOX",          (0,0),(-1,-1), 0.5, BORDER),
        ("GRID",         (0,0),(-1,-1), 0.3, BORDER),
        ("ALIGN",        (0,0),(-1,-1), "CENTER"),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",   (0,0),(-1,-1), 7),
        ("BOTTOMPADDING",(0,0),(-1,-1), 7),
    ]))
    story.append(fase_t)
    story.append(Spacer(1, 6))
    story.append(Paragraph(ciclo.get("descricao_fase", ""), just_s))

    # ─── Status bullish/bearish ───────────────────────────────────────────────
    status = data.get("status_mercado", {})
    story.append(Paragraph("📊 ESTAMOS REALMENTE EM BULLISH?", sec_s))

    e_bull   = status.get("e_bullish", True)
    classif  = status.get("classificacao", "?")
    prob_up  = status.get("prob_continuacao_alta_pct", 50)
    prob_dist= status.get("prob_distribuicao_pct", 50)
    bull_col = "#2ECC71" if e_bull else "#E74C3C"
    bull_txt = "✅ SIM" if e_bull else "❌ NÃO"

    status_data = [
        [
            Paragraph("É Bullish?", ParagraphStyle("SH", fontName="Helvetica-Bold",
                       fontSize=9, textColor=white, alignment=TA_CENTER)),
            Paragraph("Classificação", ParagraphStyle("SH", fontName="Helvetica-Bold",
                       fontSize=9, textColor=white, alignment=TA_CENTER)),
            Paragraph("Prob. Continuação Alta", ParagraphStyle("SH", fontName="Helvetica-Bold",
                       fontSize=9, textColor=white, alignment=TA_CENTER)),
            Paragraph("Prob. Distribuição", ParagraphStyle("SH", fontName="Helvetica-Bold",
                       fontSize=9, textColor=white, alignment=TA_CENTER)),
        ],
        [
            Paragraph(f"<font color='{bull_col}'><b>{bull_txt}</b></font>", center_s),
            Paragraph(f"<b>{classif}</b>", center_s),
            Paragraph(f"<font color='#2ECC71'><b>{prob_up}%</b></font>", center_s),
            Paragraph(f"<font color='#E74C3C'><b>{prob_dist}%</b></font>", center_s),
        ]
    ]
    status_t = Table(status_data, colWidths=[4.3*cm]*4)
    status_t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,0), DARK),
        ("BACKGROUND",   (0,1),(-1,1), LIGHT),
        ("BOX",          (0,0),(-1,-1), 0.5, BORDER),
        ("GRID",         (0,0),(-1,-1), 0.3, BORDER),
        ("ALIGN",        (0,0),(-1,-1), "CENTER"),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",   (0,0),(-1,-1), 7),
        ("BOTTOMPADDING",(0,0),(-1,-1), 7),
    ]))
    story.append(status_t)
    story.append(Spacer(1, 6))
    story.append(Paragraph(status.get("resposta_direta", ""), just_s))

    # ─── Confirmações técnicas ────────────────────────────────────────────────
    confs = data.get("confirmacoes_tecnicas", [])
    if confs:
        story.append(Paragraph("🔬 CONFIRMAÇÕES TÉCNICAS E ON-CHAIN", sec_s))
        conf_data = [["Indicador", "Leitura", "Sinal", "Detalhe"]]
        for c in confs:
            interp = c.get("interpretacao", "neutro").lower()
            i_col  = "#2ECC71" if "bull" in interp else "#E74C3C" if "bear" in interp else "#F1C40F"
            conf_data.append([
                Paragraph(f"<b>{c.get('indicador','')}</b>", small_s),
                Paragraph(c.get("leitura",""), small_s),
                Paragraph(f"<font color='{i_col}'><b>{c.get('interpretacao','').upper()}</b></font>",
                          ParagraphStyle("IC", fontName="Helvetica-Bold", fontSize=8.5,
                                         alignment=TA_CENTER)),
                Paragraph(c.get("detalhe",""), small_s),
            ])
        conf_t = Table(conf_data, colWidths=[3.5*cm, 2.5*cm, 2.5*cm, 9*cm])
        conf_t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0), DARK),
            ("TEXTCOLOR",     (0,0),(-1,0), white),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,0), 8.5),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [white, LIGHT]),
            ("BOX",           (0,0),(-1,-1), 0.5, BORDER),
            ("GRID",          (0,0),(-1,-1), 0.3, BORDER),
            ("ALIGN",         (0,0),(-1,-1), "LEFT"),
            ("ALIGN",         (2,0),(2,-1),  "CENTER"),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("TOPPADDING",    (0,0),(-1,-1), 5),
            ("BOTTOMPADDING", (0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 6),
        ]))
        story.append(conf_t)
    _add_curated_images(story, data.get("imagens_curatorias", []), "tecnica", small_s)
    _add_curated_images(story, data.get("imagens_curatorias", []), "onchain", small_s)

    # ─── Cenários de preço ────────────────────────────────────────────────────
    cen = data.get("cenarios_preco", {})
    if cen:
        story.append(Paragraph(
            f"🎯 CENÁRIOS DE PREÇO — {cen.get('horizonte','').upper()}", sec_s))

        cen_data = [
            [
                Paragraph("Cenário", ParagraphStyle("CH", fontName="Helvetica-Bold",
                           fontSize=9, textColor=white, alignment=TA_CENTER)),
                Paragraph("Faixa de Preço", ParagraphStyle("CH", fontName="Helvetica-Bold",
                           fontSize=9, textColor=white, alignment=TA_CENTER)),
                Paragraph("Probabilidade", ParagraphStyle("CH", fontName="Helvetica-Bold",
                           fontSize=9, textColor=white, alignment=TA_CENTER)),
                Paragraph("Condição", ParagraphStyle("CH", fontName="Helvetica-Bold",
                           fontSize=9, textColor=white, alignment=TA_CENTER)),
            ],
        ]
        for nome, chave, cor in [
            ("🚀 Otimista",    "otimista",    "#2ECC71"),
            ("📊 Base",        "base",        "#3498DB"),
            ("🛡 Conservador", "conservador", "#F1C40F"),
        ]:
            c = cen.get(chave, {})
            cen_data.append([
                Paragraph(f"<font color='{cor}'><b>{nome}</b></font>",
                          ParagraphStyle("CN", fontName="Helvetica-Bold", fontSize=9.5,
                                         alignment=TA_CENTER)),
                Paragraph(f"<b>{c.get('faixa','')}</b>",
                          ParagraphStyle("CF", fontName="Helvetica-Bold", fontSize=9.5,
                                         alignment=TA_CENTER)),
                Paragraph(f"<font color='{cor}'><b>{c.get('probabilidade_pct','')}%</b></font>",
                          ParagraphStyle("CP", fontName="Helvetica-Bold", fontSize=9.5,
                                         alignment=TA_CENTER)),
                Paragraph(c.get("condicao",""),
                          ParagraphStyle("CC", fontName="Helvetica", fontSize=8.5)),
            ])
        cen_t = Table(cen_data, colWidths=[3*cm, 3.5*cm, 3*cm, 8*cm])
        cen_t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0), DARK),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [white, LIGHT, HexColor("#FFF9E6")]),
            ("BOX",           (0,0),(-1,-1), 0.5, BORDER),
            ("GRID",          (0,0),(-1,-1), 0.3, BORDER),
            ("ALIGN",         (0,0),(2,-1),  "CENTER"),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("TOPPADDING",    (0,0),(-1,-1), 7),
            ("BOTTOMPADDING", (0,0),(-1,-1), 7),
            ("LEFTPADDING",   (3,0),(3,-1),  6),
        ]))
        story.append(cen_t)

    # ─── Topo e fundo do ciclo ────────────────────────────────────────────────
    topo  = data.get("topo_ciclo", {})
    fundo = data.get("fundo_bear_market", {})

    if topo or fundo:
        story.append(Paragraph("🏔 PROJEÇÃO DE TOPO E FUNDO DO CICLO", sec_s))
        tf_data = [
            [
                Paragraph("", body_s),
                Paragraph("<b>Faixa Provável</b>",
                          ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=9,
                                         textColor=white, alignment=TA_CENTER)),
                Paragraph("<b>Janela Temporal</b>",
                          ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=9,
                                         textColor=white, alignment=TA_CENTER)),
                Paragraph("<b>Confiança</b>",
                          ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=9,
                                         textColor=white, alignment=TA_CENTER)),
            ],
            [
                Paragraph("🏔 Topo do ciclo", ParagraphStyle("TL", fontName="Helvetica-Bold",
                           fontSize=9.5, textColor=GREEN)),
                Paragraph(f"<b>{topo.get('faixa_provavel','?')}</b>",
                          ParagraphStyle("TV", fontName="Helvetica-Bold", fontSize=9.5,
                                         textColor=GREEN, alignment=TA_CENTER)),
                Paragraph(topo.get("janela_temporal","?"),
                          ParagraphStyle("TT", fontName="Helvetica", fontSize=9,
                                         alignment=TA_CENTER)),
                Paragraph(f"<b>{topo.get('confianca_pct','?')}%</b>",
                          ParagraphStyle("TC2", fontName="Helvetica-Bold", fontSize=9.5,
                                         alignment=TA_CENTER)),
            ],
            [
                Paragraph("🕳 Fundo bear market", ParagraphStyle("TL2", fontName="Helvetica-Bold",
                           fontSize=9.5, textColor=RED)),
                Paragraph(f"<b>{fundo.get('faixa_provavel','?')}</b>",
                          ParagraphStyle("FV", fontName="Helvetica-Bold", fontSize=9.5,
                                         textColor=RED, alignment=TA_CENTER)),
                Paragraph(fundo.get("janela_temporal","?"),
                          ParagraphStyle("FT", fontName="Helvetica", fontSize=9,
                                         alignment=TA_CENTER)),
                Paragraph(f"<b>{fundo.get('confianca_pct','?')}%</b>",
                          ParagraphStyle("FC2", fontName="Helvetica-Bold", fontSize=9.5,
                                         alignment=TA_CENTER)),
            ],
        ]
        tf_t = Table(tf_data, colWidths=[4*cm, 4.5*cm, 5*cm, 4*cm])
        tf_t.setStyle(TableStyle([
            ("BACKGROUND",   (0,0),(-1,0), DARK),
            ("BACKGROUND",   (0,1),(-1,1), HexColor("#F0FFF4")),
            ("BACKGROUND",   (0,2),(-1,2), HexColor("#FFF5F5")),
            ("BOX",          (0,0),(-1,-1), 0.5, BORDER),
            ("GRID",         (0,0),(-1,-1), 0.3, BORDER),
            ("ALIGN",        (1,0),(-1,-1), "CENTER"),
            ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
            ("TOPPADDING",   (0,0),(-1,-1), 8),
            ("BOTTOMPADDING",(0,0),(-1,-1), 8),
            ("LEFTPADDING",  (0,0),(0,-1),  8),
        ]))
        story.append(tf_t)
        story.append(Spacer(1, 4))

        if topo.get("base_historica"):
            story.append(Paragraph(f"<b>Base histórica do topo:</b> {topo['base_historica']}", small_s))
        if fundo.get("base_historica"):
            story.append(Paragraph(f"<b>Base histórica do fundo:</b> {fundo['base_historica']}", small_s))
        if fundo.get("queda_estimada_pct"):
            story.append(Paragraph(
                f"<b>Queda estimada a partir do topo:</b> {fundo['queda_estimada_pct']}", small_s))

    _add_curated_images(story, data.get("imagens_curatorias", []), "ciclo", small_s)

    # ─── Invalidações ─────────────────────────────────────────────────────────
    invs = data.get("invalidacoes", [])
    if invs:
        story.append(Paragraph("⚠️ SINAIS DE INVALIDAÇÃO DA TESE", sec_s))
        for inv in invs:
            story.append(Paragraph(f"• {inv}", body_s))

    # ─── Resumo humano ────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT,
                             spaceBefore=12, spaceAfter=8))
    story.append(Paragraph("💬 RESUMO DIRETO — O QUE FAZER AGORA", sec_s))
    story.append(Paragraph(data.get("resumo_humano", ""), just_s))

    # ─── Rodapé ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"<font color='#8B949E'><i>Trinity Bot — Cycle Intelligence — "
        f"{datetime.now().strftime('%d/%m/%Y')}  •  Não é recomendação de investimento</i></font>",
        ParagraphStyle("F", fontName="Helvetica-Oblique", fontSize=7.5,
                       textColor=GRAY, alignment=TA_CENTER)
    ))

    doc.build(story)
    return buffer.getvalue()


def send_cycle_pdf(pdf_bytes: bytes, data: dict) -> bool:
    """Envia o PDF do ciclo para o Telegram."""
    fase   = data.get("ciclo", {}).get("fase_atual", "?")
    preco  = data.get("preco_atual", "?")
    e_bull = data.get("status_mercado", {}).get("e_bullish", True)
    classif= data.get("status_mercado", {}).get("classificacao", "?")
    emoji  = "🟢" if e_bull else "🔴"
    today  = datetime.now().strftime("%d/%m/%Y")

    caption = (
        f"🔄 <b>Cycle Intelligence — {today}</b>\n"
        f"₿ BTC: <b>{preco}</b>\n"
        f"{emoji} <b>{classif}</b>  •  Fase: <b>{fase}</b>\n\n"
        f"{data.get('status_mercado',{}).get('resposta_direta','')}"
    )

    url   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    files = {"document": (f"cycle_intelligence_{datetime.now().strftime('%d%m%Y')}.pdf",
                          pdf_bytes, "application/pdf")}
    dta   = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    try:
        r = requests.post(url, files=files, data=dta, timeout=30)
        return r.status_code == 200
    except Exception as e:
        print(f"Erro Telegram: {e}")
        return False


def run_cycle_intelligence():
    """Executa o Cycle Intelligence Report completo."""
    print(f"🔄 [{datetime.now().strftime('%H:%M')}] Gerando Cycle Intelligence Report...")

    print("   🔍 Analisando ciclo com IA + web search...")
    data = get_cycle_analysis()

    print("   📄 Gerando PDF...")
    pdf_bytes = generate_cycle_pdf(data)

    print("   📲 Enviando para Telegram...")
    ok = send_cycle_pdf(pdf_bytes, data)

    if ok:
        print("   ✅ Cycle Intelligence enviado!")
    else:
        print("   ❌ Erro ao enviar")


def _fallback_cycle() -> dict:
    return {
        "preco_atual": "indisponível",
        "educacao": {},
        "ciclo": {"fase_atual": "EXPANSÃO", "confianca_pct": 50,
                  "dias_desde_ultimo_halving": "?", "data_proximo_halving": "?",
                  "descricao_fase": "Dados indisponíveis."},
        "status_mercado": {"e_bullish": True, "classificacao": "NEUTRO",
                           "prob_continuacao_alta_pct": 50, "prob_distribuicao_pct": 50,
                           "resposta_direta": "Dados insuficientes para análise."},
        "confirmacoes_tecnicas": [],
        "cenarios_preco": {},
        "topo_ciclo": {},
        "fundo_bear_market": {},
        "invalidacoes": [],
        "imagens_curatorias": [],
        "resumo_humano": "Aguardar mais dados.",
    }


if __name__ == "__main__":
    run_cycle_intelligence()
