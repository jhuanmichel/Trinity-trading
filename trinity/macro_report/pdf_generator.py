"""
pdf_generator.py — Renderiza relatório macro em PDF institucional dark-theme.

Gera PDF de 8-12 páginas com:
  - Capa com branding Trinity
  - Dashboard de métricas
  - Seções de análise formatadas
  - Veredito final com dashboard de decisão
"""
import logging
from pathlib import Path
from datetime import datetime, timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.pdfgen import canvas

log = logging.getLogger(__name__)

# Cores
BG      = HexColor("#0d1117")
CARD    = HexColor("#161b22")
SURFACE = HexColor("#1c2333")
GREEN   = HexColor("#00ffb2")
RED     = HexColor("#ff4d4f")
YELLOW  = HexColor("#ffd700")
ORANGE  = HexColor("#ff9100")
BLUE    = HexColor("#58a6ff")
TEXT    = HexColor("#e6edf3")
DIM     = HexColor("#8b949e")
BORDER  = HexColor("#30363d")
WHITE   = HexColor("#ffffff")

W, H  = A4
FONT  = "Helvetica"
BOLD  = "Helvetica-Bold"


def _draw_bg(c, doc):
    """Callback onPage: fundo dark + linha verde header + footer numerado."""
    c.saveState()
    # Fundo escuro
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=True, stroke=False)
    # Linha verde no topo
    c.setStrokeColor(GREEN)
    c.setLineWidth(0.4)
    c.line(18 * mm, H - 14 * mm, W - 18 * mm, H - 14 * mm)
    # Linha inferior
    c.setStrokeColor(BORDER)
    c.line(18 * mm, 13 * mm, W - 18 * mm, 13 * mm)
    # Footer text
    c.setFillColor(DIM)
    c.setFont(FONT, 7)
    c.drawString(18 * mm, 9 * mm, "TRINITY TRADING — Institutional Macro Report — CONFIDENCIAL")
    c.drawRightString(W - 18 * mm, 9 * mm, f"p.{doc.page}")
    c.restoreState()


def _s() -> dict:
    """Retorna dict de estilos."""
    return {
        "title": ParagraphStyle(
            "t", fontName=BOLD, fontSize=28, textColor=WHITE,
            alignment=TA_CENTER, leading=34
        ),
        "subtitle": ParagraphStyle(
            "st", fontName=FONT, fontSize=13, textColor=DIM,
            alignment=TA_CENTER, leading=18
        ),
        "date": ParagraphStyle(
            "d", fontName=FONT, fontSize=10, textColor=GREEN,
            alignment=TA_CENTER, leading=14
        ),
        "h1": ParagraphStyle(
            "h1", fontName=BOLD, fontSize=16, textColor=GREEN,
            spaceBefore=16, spaceAfter=8, leading=20
        ),
        "h2": ParagraphStyle(
            "h2", fontName=BOLD, fontSize=12, textColor=BLUE,
            spaceBefore=10, spaceAfter=5, leading=16
        ),
        "body": ParagraphStyle(
            "b", fontName=FONT, fontSize=9.5, textColor=TEXT,
            alignment=TA_JUSTIFY, leading=13, spaceBefore=2, spaceAfter=2
        ),
        "body_b": ParagraphStyle(
            "bb", fontName=BOLD, fontSize=9.5, textColor=WHITE, leading=13
        ),
        "small": ParagraphStyle(
            "sm", fontName=FONT, fontSize=7.5, textColor=DIM, leading=10
        ),
        "disc": ParagraphStyle(
            "disc", fontName=FONT, fontSize=6.5, textColor=DIM,
            alignment=TA_CENTER, leading=8, spaceBefore=15
        ),
        "th": ParagraphStyle(
            "th", fontName=BOLD, fontSize=8.5, textColor=WHITE,
            alignment=TA_CENTER, leading=11
        ),
        "tc": ParagraphStyle(
            "tc", fontName=FONT, fontSize=8.5, textColor=TEXT,
            alignment=TA_CENTER, leading=11
        ),
        "tcl": ParagraphStyle(
            "tcl", fontName=FONT, fontSize=8.5, textColor=TEXT,
            alignment=TA_LEFT, leading=11
        ),
        "mv": ParagraphStyle(
            "mv", fontName=BOLD, fontSize=20, textColor=WHITE,
            alignment=TA_CENTER, leading=24
        ),
        "ml": ParagraphStyle(
            "ml", fontName=FONT, fontSize=7.5, textColor=DIM,
            alignment=TA_CENTER, leading=10
        ),
        "verdict": ParagraphStyle(
            "v", fontName=BOLD, fontSize=12, textColor=ORANGE,
            alignment=TA_CENTER, leading=16, spaceBefore=8, spaceAfter=8
        ),
    }


def _tbl(data, widths, hdr_bg=SURFACE):
    """Cria tabela estilizada."""
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), hdr_bg),
        ("BACKGROUND", (0, 1), (-1, -1), CARD),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ])
    for i in range(2, len(data), 2):
        style.add("BACKGROUND", (0, i), (-1, i), SURFACE)
    t = Table(data, colWidths=widths)
    t.setStyle(style)
    return t


def _text_to_paragraphs(text: str, style) -> list:
    """Converte texto multi-linha em lista de Paragraphs."""
    if not text:
        return [Paragraph("<i>Secao nao disponivel</i>", style)]

    elements = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        # Escapar caracteres XML especiais
        para = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Limpar markdown bold
        para = para.replace("**", "").replace("__", "")
        elements.append(Paragraph(para, style))
        elements.append(Spacer(1, 2 * mm))

    return elements if elements else [Paragraph("<i>Sem dados</i>", style)]


def generate_pdf(collected_data: dict, analysis: dict, output_path: str = None) -> str:
    """
    Gera PDF institucional.

    Args:
        collected_data: output do data_collector
        analysis: output do analysis_engine
        output_path: caminho do PDF (default: dashboard/reports/macro_YYYYMMDD_HHMM.pdf)

    Returns:
        Path do PDF gerado
    """
    now = datetime.now(timezone.utc)

    if not output_path:
        reports_dir = Path(__file__).parent.parent.parent / "dashboard" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(reports_dir / f"macro_{now.strftime('%Y%m%d_%H%M')}.pdf")

    st = _s()

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm,
        leftMargin=18 * mm, rightMargin=18 * mm,
    )

    story = []

    # ─────────────────────────────────────────────────────────────────────
    # CAPA
    # ─────────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 55 * mm))
    story.append(Paragraph("TRINITY TRADING", st["title"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("WEEKLY MACRO REPORT", st["subtitle"]))
    story.append(Spacer(1, 12 * mm))
    story.append(Paragraph(
        '<font color="#ffd700">BITCOIN &amp; CRYPTO — ANALISE INSTITUCIONAL</font>',
        ParagraphStyle("ct", fontName=BOLD, fontSize=14, textColor=YELLOW,
                       alignment=TA_CENTER, leading=18)
    ))
    story.append(Spacer(1, 4 * mm))

    week_num  = now.isocalendar()[1]
    date_display = collected_data.get("metadata", {}).get("date_display", now.strftime("%d/%m/%Y %H:%M UTC"))
    story.append(Paragraph(f"Semana {week_num} — {date_display}", st["date"]))
    story.append(Spacer(1, 20 * mm))
    story.append(Paragraph(
        "Classificacao: CONFIDENCIAL — Uso interno",
        ParagraphStyle("c", fontName=BOLD, fontSize=8, textColor=RED, alignment=TA_CENTER)
    ))
    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────────────
    # DASHBOARD DE MÉTRICAS
    # ─────────────────────────────────────────────────────────────────────
    story.append(Paragraph("DASHBOARD DE METRICAS", st["h1"]))
    story.append(Spacer(1, 3 * mm))

    btc    = collected_data.get("btc_market", {})
    macro  = collected_data.get("macro_fred", {})
    regime = collected_data.get("regime_trinity", {})

    def _c(val, color):
        return f'<font color="{color}">{val}</font>'

    # Linha 1: BTC price, 24h change, NFCI, M2
    # Fallback: se macro_fred não tiver o dado (FRED falhou), usa regime_trinity
    nfci_val = macro.get("nfci_current") or regime.get("nfci_value", 0) or 0
    m2_yoy   = macro.get("m2_yoy_pct") or regime.get("m2_yoy_pct", 0) or 0

    m1 = [
        [
            Paragraph(_c(f"${btc.get('price', 0):,.0f}", "#ffffff"), st["mv"]),
            Paragraph(_c(f"{btc.get('change_24h_pct', 0):+.1f}%",
                         "#ff4d4f" if btc.get("change_24h_pct", 0) < 0 else "#00c853"), st["mv"]),
            Paragraph(_c(f"{nfci_val:.3f}",
                         "#00c853" if nfci_val < 0 else "#ff4d4f"), st["mv"]),
            Paragraph(_c(f"{m2_yoy:+.1f}%",
                         "#00c853" if m2_yoy > 0 else "#ff4d4f"), st["mv"]),
        ],
        [
            Paragraph("BTC Price", st["ml"]),
            Paragraph("24h Change", st["ml"]),
            Paragraph("NFCI", st["ml"]),
            Paragraph("M2 YoY", st["ml"]),
        ],
    ]
    t1 = Table(m1, colWidths=[40 * mm] * 4)
    t1.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
    ]))
    story.append(t1)
    story.append(Spacer(1, 3 * mm))

    # Linha 2: Regime, Bull Band, vs Band, Sazonalidade
    bb_dist = regime.get("bull_band_dist", 0) or 0
    m2 = [
        [
            Paragraph(_c(regime.get("direction", "?"), "#ffd700"), st["mv"]),
            Paragraph(_c(regime.get("bull_band_bias", "?"),
                         "#ff4d4f" if regime.get("bull_band_bias") == "BEAR" else "#00c853"), st["mv"]),
            Paragraph(_c(f"{bb_dist:+.1f}%",
                         "#ff4d4f" if bb_dist < 0 else "#00c853"), st["mv"]),
            Paragraph(_c(f"x{regime.get('seasonality_mult', 1):.2f}", "#8b949e"), st["mv"]),
        ],
        [
            Paragraph("BTC Regime", st["ml"]),
            Paragraph("Bull Band", st["ml"]),
            Paragraph("vs Band", st["ml"]),
            Paragraph("Sazonalidade", st["ml"]),
        ],
    ]
    t2 = Table(m2, colWidths=[40 * mm] * 4)
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
    ]))
    story.append(t2)
    story.append(Spacer(1, 4 * mm))

    # Sinais do Regime Engine
    sigs = regime.get("signals", [])
    if sigs:
        story.append(Paragraph("Sinais do Regime Engine:", st["h2"]))
        for sig in sigs[:6]:
            sig_clean = sig.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(f"• {sig_clean}", st["body"]))
        story.append(Spacer(1, 3 * mm))

    # Métricas extras: Yields, Funding, OI
    t10y     = macro.get("treasury_10y")
    t2y      = macro.get("treasury_2y")
    spread   = macro.get("yield_spread_10y_2y")
    vix      = macro.get("vix")
    hy       = macro.get("hy_spread")
    funding  = btc.get("funding_rate", 0) or 0
    oi       = btc.get("oi_usd", 0) or 0

    extra_rows = []
    if t10y:
        extra_rows.append(["Treasury 10Y", f"{t10y:.2f}%",
                           "Treasury 2Y", f"{t2y:.2f}%" if t2y else "N/A"])
    if spread is not None:
        spread_color = "verde" if spread > 0 else "vermelho"
        extra_rows.append(["Spread 10Y-2Y", f"{spread:.3f}%",
                           "VIX", f"{vix:.1f}" if vix else "N/A"])
    if hy:
        extra_rows.append(["HY Credit Spread", f"{hy:.2f}%",
                           "BTC Funding/yr", f"{btc.get('funding_ann', 0):+.1f}%"])
    if oi > 0:
        extra_rows.append(["BTC OI", f"${oi/1e9:.2f}B",
                           "Alts avg 24h", f"{collected_data.get('alt_market',{}).get('avg_change_pct',0):+.2f}%"])

    if extra_rows:
        story.append(Paragraph("Indicadores Macro & Derivativos:", st["h2"]))
        hdr = [
            Paragraph("INDICADOR", st["th"]),
            Paragraph("VALOR", st["th"]),
            Paragraph("INDICADOR", st["th"]),
            Paragraph("VALOR", st["th"]),
        ]
        tbl_data = [hdr] + [
            [Paragraph(str(r[0]), st["tcl"]), Paragraph(str(r[1]), st["tc"]),
             Paragraph(str(r[2]), st["tcl"]), Paragraph(str(r[3]), st["tc"])]
            for r in extra_rows
        ]
        story.append(_tbl(tbl_data, [55 * mm, 25 * mm, 55 * mm, 25 * mm]))
        story.append(Spacer(1, 3 * mm))

    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────────────
    # SEÇÕES DE ANÁLISE
    # ─────────────────────────────────────────────────────────────────────
    section_titles = [
        ("executive_summary", "1. EXECUTIVE SUMMARY"),
        ("regime_macro",      "2. REGIME MACRO GLOBAL"),
        ("mercado_crypto",    "3. MERCADO CRYPTO & DERIVATIVOS"),
        ("ciclo_position",    "4. POSICAO NO CICLO"),
        ("cenarios",          "5. CENARIOS PROBABILISTICOS"),
        ("veredito",          "6. VEREDITO FINAL"),
    ]

    for key, title in section_titles:
        story.append(Paragraph(title, st["h1"]))
        story.append(Spacer(1, 2 * mm))

        text = analysis.get(key, "")
        if text and text not in ("Analise requer Claude API key.", "Análise requer Claude API key."):
            paras = _text_to_paragraphs(text, st["body"])
            story.extend(paras)
        else:
            story.append(Paragraph(
                "<i>Secao requer Claude API key configurada. "
                "Adicione ANTHROPIC_API_KEY ao config.py ou env vars do Render.</i>",
                st["small"]
            ))

        story.append(Spacer(1, 4 * mm))

        # Page break após seções longas
        if key in ("regime_macro", "ciclo_position", "cenarios"):
            story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────────────
    # DISCLAIMER
    # ─────────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "DISCLAIMER: Este relatorio e gerado automaticamente pelo Trinity Trading Bot "
        "usando dados publicos (FRED, MEXC, Binance) e analise via Claude AI. "
        "Nao constitui recomendacao de investimento. Criptomoedas sao ativos de alta "
        "volatilidade e risco. Consulte um profissional financeiro qualificado.",
        st["disc"]
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        f'<font color="#00ffb2">TRINITY TRADING</font> — {date_display}',
        ParagraphStyle("f", fontName=BOLD, fontSize=7, textColor=DIM, alignment=TA_CENTER)
    ))

    # ─────────────────────────────────────────────────────────────────────
    # BUILD PDF
    # ─────────────────────────────────────────────────────────────────────
    doc.build(story, onFirstPage=_draw_bg, onLaterPages=_draw_bg)
    log.info(f"[MacroReport] PDF gerado: {output_path}")
    return output_path
