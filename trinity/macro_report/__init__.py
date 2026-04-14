"""
macro_report — Módulo de Relatório Macro Semanal Institucional

Uso:
  from trinity.macro_report import generate_macro_report
  pdf_path = generate_macro_report()

Pipeline:
  1. data_collector.collect_all()       → dados de 8+ fontes
  2. analysis_engine.generate_analysis() → análise via Claude API
  3. pdf_generator.generate_pdf()        → PDF institucional dark-theme
"""
import logging

log = logging.getLogger(__name__)


def generate_macro_report(output_path: str = None) -> str:
    """
    Gera relatório macro completo.

    Args:
        output_path: caminho do PDF (default: dashboard/reports/macro_YYYYMMDD_HHMM.pdf)

    Returns:
        Path absoluto do PDF gerado
    """
    log.info("[MacroReport] Iniciando geração de relatório...")

    # 1. Coletar dados
    from trinity.macro_report.data_collector import collect_all
    data = collect_all()
    log.info(
        f"[MacroReport] Dados coletados: {data['metadata']['sources_collected']} fontes "
        f"em {data['metadata']['collection_time_s']}s"
    )

    # 2. Gerar análise via Claude
    from trinity.macro_report.analysis_engine import generate_analysis
    analysis = generate_analysis(data)
    log.info(f"[MacroReport] Análise gerada: {len(analysis.get('raw_response', ''))} chars")

    # 3. Gerar PDF
    from trinity.macro_report.pdf_generator import generate_pdf
    pdf_path = generate_pdf(data, analysis, output_path)
    log.info(f"[MacroReport] PDF pronto: {pdf_path}")

    return pdf_path
