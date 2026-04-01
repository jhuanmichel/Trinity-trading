"""
morning_brief.py — Morning Brief Diário
Gera um PDF profissional com análise de mercado e envia ao Telegram às 8h (Brasília).
Cobre: geopolítica, macro, baleias/institucional, análise técnica do dia.
"""

import os
import json
import re
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
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

load_dotenv()

# Tamanho máximo das imagens curadas no PDF (cm)
IMG_MAX_WIDTH_CM = 17
IMG_MAX_HEIGHT_CM = 11

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Cores ────────────────────────────────────────────────────────────────────
DARK_BG     = HexColor("#0D1117")
CARD_BG     = HexColor("#161B22")
ACCENT      = HexColor("#F7931A")   # laranja bitcoin
GREEN       = HexColor("#2ECC71")
RED         = HexColor("#E74C3C")
BLUE        = HexColor("#3498DB")
YELLOW      = HexColor("#F1C40F")
GRAY        = HexColor("#8B949E")
LIGHT       = HexColor("#E6EDF3")


def _download_image(url: str) -> Optional[bytes]:
    """Baixa imagem por URL e retorna bytes, ou None se falhar."""
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
    """Cria um Image do ReportLab a partir dos bytes, redimensionado para caber na página."""
    try:
        from PIL import Image as PILImage
        from reportlab.lib.units import cm
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
        rl_img = RLImage(stream, width=nw, height=nh)
        return rl_img
    except Exception:
        return None


def _add_curated_images(story, imagens: list, posicao: str, body_style, small_style):
    """Insere imagens curadas na posição indicada, com legenda em português."""
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
                story.append(Paragraph(f"<i>{legenda_pt}</i>", small_style))
                story.append(Spacer(1, 6))
        else:
            story.append(Paragraph(f"<i>[Gráfico: {legenda_pt}]</i>", small_style))
            story.append(Spacer(1, 4))


def get_market_brief() -> dict:
    """Usa Claude com web search para coletar e analisar as notícias do dia."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    today = datetime.now().strftime("%d/%m/%Y")

    prompt = f"""Hoje é {today}. Você é um analista sênior de mercado financeiro especializado em Bitcoin e criptomoedas.

Pesquise e analise as notícias e dados mais relevantes das últimas 24 horas que podem impactar o preço do Bitcoin e do mercado cripto.

Em seguida, usando web search, selecione de 0 a 6 imagens analíticas relevantes do dia (gráficos públicos de TradingView, Glassnode, CryptoQuant, Coinglass, The Block ou fontes similares). Para cada imagem, aplique o filtro de aprovação: (1) é relevante hoje? (2) melhora o entendimento? (3) acrescenta algo que o texto não entrega? (4) não repete outra imagem? A imagem mais importante deve aparecer primeiro (âncora visual). Em dias sem novidade visual relevante, use menos imagens — nunca force visual. Retorne a URL direta da imagem (URL que termina em .png, .jpg, .jpeg ou .webp quando possível).

Retorne APENAS um JSON válido com esta estrutura exata:

{{
  "resumo_executivo": "2-3 frases resumindo o cenário geral do dia para crypto",
  "sentimento_geral": "BULLISH" | "BEARISH" | "NEUTRO",
  "score_do_dia": <número de 0 a 100>,
  "geopolitica": [
    {{
      "titulo": "título da notícia",
      "impacto": "POSITIVO" | "NEGATIVO" | "NEUTRO",
      "descricao": "2-3 frases explicando o impacto no Bitcoin",
      "fonte": "nome da fonte"
    }}
  ],
  "macro": [
    {{
      "titulo": "evento/dado macro",
      "impacto": "POSITIVO" | "NEGATIVO" | "NEUTRO", 
      "descricao": "2-3 frases explicando relevância para crypto",
      "fonte": "nome da fonte"
    }}
  ],
  "baleias_institucional": [
    {{
      "titulo": "movimento identificado",
      "impacto": "POSITIVO" | "NEGATIVO" | "NEUTRO",
      "descricao": "2-3 frases sobre o movimento",
      "fonte": "nome da fonte"
    }}
  ],
  "analise_tecnica": {{
    "tendencia": "ALTA" | "BAIXA" | "LATERAL",
    "suporte_principal": "$XX,XXX",
    "resistencia_principal": "$XX,XXX",
    "nivel_critico": "$XX,XXX",
    "descricao": "3-4 frases sobre o cenário técnico de hoje",
    "pontos_atencao": ["ponto 1", "ponto 2", "ponto 3"]
  }},
  "eventos_agenda": [
    {{
      "horario": "HH:MM BRT",
      "evento": "nome do evento",
      "relevancia": "ALTA" | "MEDIA" | "BAIXA"
    }}
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
  "conclusao": "Parágrafo final com recomendação de postura para o trader hoje"
}}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )

    # Extrai o texto da resposta
    full_text = ""
    for block in message.content:
        if block.type == "text":
            full_text += block.text

    # Parse do JSON — múltiplas estratégias de extração
    raw = full_text.strip()

    def _try_parse(text: str) -> dict | None:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    # Estratégia 1: resposta já é JSON puro
    result = _try_parse(raw)
    if result:
        return result

    # Estratégia 2: JSON dentro de bloco markdown ```json ... ```
    md_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if md_match:
        result = _try_parse(md_match.group(1))
        if result:
            return result

    # Estratégia 3: primeiro objeto JSON válido no texto
    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        result = _try_parse(brace_match.group(0))
        if result:
            return result

    # Estratégia 4: encontrar última ocorrência de { até } (resposta com prefixo de texto)
    last_open = raw.rfind("{")
    if last_open != -1:
        result = _try_parse(raw[last_open:])
        if result:
            return result

    print(f"⚠️  Falha ao parsear JSON da API. Resposta bruta (primeiros 500 chars):\n{raw[:500]}")
    return _fallback_brief()


def generate_pdf(brief: dict) -> bytes:
    """Gera o PDF do Morning Brief e retorna os bytes."""
    buffer = BytesIO()
    today  = datetime.now().strftime("%d de %B de %Y").lower()
    today  = today[0].upper() + today[1:]

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm,
        title=f"Morning Brief — {today}",
    )

    # ─── Estilos ──────────────────────────────────────────────────────────────
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleStyle",
        fontName="Helvetica-Bold",
        fontSize=22,
        textColor=ACCENT,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "SubtitleStyle",
        fontName="Helvetica",
        fontSize=11,
        textColor=GRAY,
        alignment=TA_LEFT,
        spaceAfter=2,
    )
    section_style = ParagraphStyle(
        "SectionStyle",
        fontName="Helvetica-Bold",
        fontSize=13,
        textColor=ACCENT,
        spaceBefore=14,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "BodyStyle",
        fontName="Helvetica",
        fontSize=9.5,
        textColor=black,
        leading=14,
        spaceAfter=4,
    )
    card_title_style = ParagraphStyle(
        "CardTitle",
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=black,
        spaceAfter=2,
    )
    small_style = ParagraphStyle(
        "SmallStyle",
        fontName="Helvetica",
        fontSize=8.5,
        textColor=HexColor("#555555"),
        spaceAfter=2,
    )
    conclusion_style = ParagraphStyle(
        "ConclusionStyle",
        fontName="Helvetica",
        fontSize=10,
        textColor=black,
        leading=15,
        spaceAfter=4,
    )

    story = []

    # ─── Cabeçalho ────────────────────────────────────────────────────────────
    sentimento = brief.get("sentimento_geral", "NEUTRO")
    score      = brief.get("score_do_dia", 50)
    sent_color = GREEN if sentimento == "BULLISH" else RED if sentimento == "BEARISH" else YELLOW

    header_data = [[
        Paragraph(f"<b>₿ MORNING BRIEF</b>", title_style),
        Paragraph(
            f"<font color='#{sent_color.hexval()[2:]}'>● {sentimento}</font>  "
            f"<font color='#8B949E'>Score: {score}/100</font>",
            ParagraphStyle("H", fontName="Helvetica-Bold", fontSize=12,
                           textColor=black, alignment=TA_RIGHT)
        ),
    ]]
    header_table = Table(header_data, colWidths=[10*cm, 8*cm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(header_table)
    story.append(Paragraph(f"{today}  •  Análise para traders de Bitcoin &amp; Crypto", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=ACCENT, spaceAfter=8))

    # ─── Resumo executivo ─────────────────────────────────────────────────────
    story.append(Paragraph("📋 RESUMO EXECUTIVO", section_style))
    story.append(Paragraph(brief.get("resumo_executivo", ""), body_style))
    _add_curated_images(story, brief.get("imagens_curatorias", []), "inicio", body_style, small_style)

    # ─── Helper para seções de notícias ───────────────────────────────────────
    def add_news_section(title, items, emoji):
        if not items:
            return
        story.append(Paragraph(f"{emoji} {title}", section_style))
        for item in items:
            impact     = item.get("impacto", "NEUTRO")
            imp_color  = "#2ECC71" if impact == "POSITIVO" else "#E74C3C" if impact == "NEGATIVO" else "#F1C40F"
            imp_label  = "▲ POSITIVO" if impact == "POSITIVO" else "▼ NEGATIVO" if impact == "NEGATIVO" else "● NEUTRO"

            row = [[
                Paragraph(
                    f"<b>{item.get('titulo','')}</b>  "
                    f"<font color='{imp_color}'>{imp_label}</font>",
                    card_title_style
                ),
            ],[
                Paragraph(item.get("descricao",""), body_style),
            ],[
                Paragraph(f"Fonte: {item.get('fonte','')}", small_style),
            ]]
            card = Table([[r[0]] for r in row], colWidths=[17.5*cm])
            card.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,-1), HexColor("#F8F9FA")),
                ("BOX",           (0,0), (-1,-1), 0.5, HexColor("#DEE2E6")),
                ("LEFTPADDING",   (0,0), (-1,-1), 8),
                ("RIGHTPADDING",  (0,0), (-1,-1), 8),
                ("TOPPADDING",    (0,0), (0,0),   6),
                ("BOTTOMPADDING", (0,-1),(-1,-1), 6),
            ]))
            story.append(KeepTogether([card, Spacer(1, 5)]))

    add_news_section("GEOPOLÍTICA", brief.get("geopolitica", []), "🌍")
    add_news_section("DADOS MACRO", brief.get("macro", []), "📊")
    add_news_section("BALEIAS & INSTITUCIONAL", brief.get("baleias_institucional", []), "🐋")
    _add_curated_images(story, brief.get("imagens_curatorias", []), "onchain", body_style, small_style)

    # ─── Análise técnica ──────────────────────────────────────────────────────
    at = brief.get("analise_tecnica", {})
    if at:
        story.append(Paragraph("📈 ANÁLISE TÉCNICA DO DIA", section_style))

        tend  = at.get("tendencia","")
        t_col = "#2ECC71" if tend == "ALTA" else "#E74C3C" if tend == "BAIXA" else "#F1C40F"

        tech_data = [
            ["Tendência", f"Suporte", "Resistência", "Nível Crítico"],
            [
                Paragraph(f"<font color='{t_col}'><b>{tend}</b></font>",
                          ParagraphStyle("TC", fontName="Helvetica-Bold", fontSize=10, alignment=TA_CENTER)),
                Paragraph(f"<b>{at.get('suporte_principal','')}</b>",
                          ParagraphStyle("TC", fontName="Helvetica-Bold", fontSize=10, alignment=TA_CENTER, textColor=GREEN)),
                Paragraph(f"<b>{at.get('resistencia_principal','')}</b>",
                          ParagraphStyle("TC", fontName="Helvetica-Bold", fontSize=10, alignment=TA_CENTER, textColor=RED)),
                Paragraph(f"<b>{at.get('nivel_critico','')}</b>",
                          ParagraphStyle("TC", fontName="Helvetica-Bold", fontSize=10, alignment=TA_CENTER, textColor=YELLOW)),
            ]
        ]
        tech_table = Table(tech_data, colWidths=[4.3*cm]*4)
        tech_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0),   HexColor("#343A40")),
            ("TEXTCOLOR",     (0,0), (-1,0),   white),
            ("FONTNAME",      (0,0), (-1,0),   "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,0),   9),
            ("ALIGN",         (0,0), (-1,-1),  "CENTER"),
            ("VALIGN",        (0,0), (-1,-1),  "MIDDLE"),
            ("BACKGROUND",    (0,1), (-1,1),   HexColor("#F8F9FA")),
            ("BOX",           (0,0), (-1,-1),  0.5, HexColor("#DEE2E6")),
            ("GRID",          (0,0), (-1,-1),  0.3, HexColor("#DEE2E6")),
            ("TOPPADDING",    (0,0), (-1,-1),  6),
            ("BOTTOMPADDING", (0,0), (-1,-1),  6),
        ]))
        story.append(tech_table)
        story.append(Spacer(1, 6))
        story.append(Paragraph(at.get("descricao",""), body_style))

        pontos = at.get("pontos_atencao", [])
        if pontos:
            story.append(Paragraph("<b>⚠️ Pontos de atenção:</b>", body_style))
            for p in pontos:
                story.append(Paragraph(f"• {p}", body_style))
        _add_curated_images(story, brief.get("imagens_curatorias", []), "tecnica", body_style, small_style)

    # ─── Agenda do dia ────────────────────────────────────────────────────────
    eventos = brief.get("eventos_agenda", [])
    if eventos:
        story.append(Paragraph("🗓 AGENDA DO DIA", section_style))
        ev_data = [["Horário", "Evento", "Relevância"]]
        for ev in eventos:
            rel   = ev.get("relevancia","MEDIA")
            r_col = "#E74C3C" if rel == "ALTA" else "#F1C40F" if rel == "MEDIA" else "#8B949E"
            ev_data.append([
                Paragraph(ev.get("horario",""), small_style),
                Paragraph(ev.get("evento",""), small_style),
                Paragraph(f"<font color='{r_col}'><b>{rel}</b></font>",
                          ParagraphStyle("RC", fontName="Helvetica-Bold", fontSize=8.5, alignment=TA_CENTER)),
            ])
        ev_table = Table(ev_data, colWidths=[3*cm, 11*cm, 3.5*cm])
        ev_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0),   HexColor("#343A40")),
            ("TEXTCOLOR",     (0,0), (-1,0),   white),
            ("FONTNAME",      (0,0), (-1,0),   "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,0),   8.5),
            ("ALIGN",         (0,0), (-1,-1),  "CENTER"),
            ("VALIGN",        (0,0), (-1,-1),  "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1), (-1,-1),  [white, HexColor("#F8F9FA")]),
            ("BOX",           (0,0), (-1,-1),  0.5, HexColor("#DEE2E6")),
            ("GRID",          (0,0), (-1,-1),  0.3, HexColor("#DEE2E6")),
            ("TOPPADDING",    (0,0), (-1,-1),  5),
            ("BOTTOMPADDING", (0,0), (-1,-1),  5),
            ("LEFTPADDING",   (0,0), (-1,-1),  6),
        ]))
        story.append(ev_table)

    # ─── Conclusão ────────────────────────────────────────────────────────────
    _add_curated_images(story, brief.get("imagens_curatorias", []), "ciclo", body_style, small_style)
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceBefore=12, spaceAfter=8))
    story.append(Paragraph("💡 POSTURA RECOMENDADA PARA HOJE", section_style))
    story.append(Paragraph(brief.get("conclusao",""), conclusion_style))

    # ─── Rodapé ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"<font color='#8B949E'><i>Gerado por Trinity Bot — {datetime.now().strftime('%d/%m/%Y %H:%M')} BRT  •  Não é recomendação de investimento</i></font>",
        ParagraphStyle("Footer", fontName="Helvetica-Oblique", fontSize=7.5,
                       textColor=GRAY, alignment=TA_CENTER)
    ))

    doc.build(story)
    return buffer.getvalue()


def send_pdf_telegram(pdf_bytes: bytes, caption: str) -> bool:
    """Envia o PDF como documento para o Telegram."""
    url   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    today = datetime.now().strftime("%d-%m-%Y")
    files = {"document": (f"morning_brief_{today}.pdf", pdf_bytes, "application/pdf")}
    data  = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    try:
        r = requests.post(url, files=files, data=data, timeout=30)
        return r.status_code == 200
    except Exception as e:
        print(f"Erro Telegram: {e}")
        return False


def run_morning_brief():
    """Executa o Morning Brief completo."""
    print(f"☀️ [{datetime.now().strftime('%H:%M')}] Gerando Morning Brief...")

    print("   🔍 Pesquisando notícias com IA...")
    brief = get_market_brief()

    print("   📄 Gerando PDF...")
    pdf_bytes = generate_pdf(brief)

    sentimento = brief.get("sentimento_geral", "NEUTRO")
    score      = brief.get("score_do_dia", 50)
    emoji      = "🟢" if sentimento == "BULLISH" else "🔴" if sentimento == "BEARISH" else "🟡"

    caption = (
        f"☀️ <b>Morning Brief — {datetime.now().strftime('%d/%m/%Y')}</b>\n"
        f"{emoji} Sentimento: <b>{sentimento}</b> | Score: <b>{score}/100</b>\n\n"
        f"{brief.get('resumo_executivo','')}"
    )

    print("   📲 Enviando para Telegram...")
    ok = send_pdf_telegram(pdf_bytes, caption)

    if ok:
        print("   ✅ Morning Brief enviado com sucesso!")
    else:
        print("   ❌ Erro ao enviar para Telegram")


def _fallback_brief() -> dict:
    return {
        "resumo_executivo": "Dados indisponíveis no momento.",
        "sentimento_geral": "NEUTRO",
        "score_do_dia": 50,
        "geopolitica": [],
        "macro": [],
        "baleias_institucional": [],
        "analise_tecnica": {},
        "eventos_agenda": [],
        "imagens_curatorias": [],
        "conclusao": "Aguardar mais dados antes de operar.",
    }


if __name__ == "__main__":
    run_morning_brief()
