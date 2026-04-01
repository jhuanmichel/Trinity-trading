"""
scoring.py — MÓDULO 8: Motor de Score Probabilístico
Combina todos os módulos em um score unificado de LONG / SHORT / NO TRADE.
"""
from config import WEIGHTS


def calculate_score(analyses: dict) -> dict:
    """
    Recebe o dict com a análise de todos os módulos e calcula o score final.

    Args:
        analyses: {
            "regime":      {..., "score": 0-100},
            "trend":       {..., "score": 0-100},
            "momentum":    {..., "score": 0-100},
            "volume":      {..., "score": 0-100},
            "derivatives": {..., "score": 0-100},
            "onchain":     {..., "score": 0-100},
            "sentiment":   {..., "score": 0-100},
        }

    Returns:
        dict com score_long, score_short, signal, confidence
    """
    total_weight = sum(WEIGHTS.values())
    weighted_sum = 0

    breakdown = {}
    for module, weight in WEIGHTS.items():
        module_data  = analyses.get(module, {})
        module_score = module_data.get("score", 50)  # 50 = neutro se indisponível
        contribution = (module_score * weight) / total_weight
        weighted_sum += contribution

        breakdown[module] = {
            "score":        module_score,
            "weight":       weight,
            "contribution": round(contribution, 2),
        }

    # Score final: 0-100 (50 = neutro, >50 = tendência long, <50 = short)
    final_score = round(weighted_sum, 1)

    # Convertendo para probabilidades
    prob_long  = round(final_score, 1)
    prob_short = round(100 - final_score, 1)

    # Sinal final
    signal, confidence = _classify_signal(final_score, analyses)

    return {
        "final_score": final_score,
        "prob_long":   prob_long,
        "prob_short":  prob_short,
        "signal":      signal,
        "confidence":  confidence,
        "breakdown":   breakdown,
        "summary":     _format_summary(final_score, prob_long, prob_short, signal, confidence, breakdown),
    }


def _classify_signal(score: float, analyses: dict):
    """
    Classifica o sinal e nível de confiança.
    Lógica especial: se ADX < 20 (lateral), prefere NO TRADE ou range trade.
    """
    regime = analyses.get("regime", {})
    adx    = regime.get("adx", 25)

    # Mercado lateral: score precisa ser mais extremo para gerar sinal
    if adx < 20:
        if score >= 72:    return "LONG",    "MÉDIA (mercado lateral)"
        if score <= 28:    return "SHORT",   "MÉDIA (mercado lateral)"
        return "NO TRADE",  "LATERAL — aguardar rompimento"

    # Mercado com tendência
    if score >= 75:    return "LONG",     "ALTA"
    if score >= 65:    return "LONG",     "MÉDIA"
    if score >= 58:    return "LONG",     "BAIXA (aguardar confirmação)"
    if score <= 25:    return "SHORT",    "ALTA"
    if score <= 35:    return "SHORT",    "MÉDIA"
    if score <= 42:    return "SHORT",    "BAIXA (aguardar confirmação)"
    return "NO TRADE",     "ZONA INDEFINIDA"


def _format_summary(score, prob_long, prob_short, signal, confidence, breakdown) -> str:
    bar_long  = "█" * int(prob_long  / 5) + "░" * (20 - int(prob_long  / 5))
    bar_short = "█" * int(prob_short / 5) + "░" * (20 - int(prob_short / 5))

    lines = [
        f"{'='*50}",
        f"  SCORE FINAL: {score}/100",
        f"  LONG  [{bar_long}] {prob_long:.1f}%",
        f"  SHORT [{bar_short}] {prob_short:.1f}%",
        f"  SINAL: {signal} | Confiança: {confidence}",
        f"{'='*50}",
        "  BREAKDOWN POR MÓDULO:",
    ]
    for module, data in breakdown.items():
        bar = "█" * int(data["score"] / 10) + "░" * (10 - int(data["score"] / 10))
        lines.append(f"  {module:<12} [{bar}] {data['score']:3d}/100  (peso {data['weight']}%)")
    lines.append(f"{'='*50}")

    return "\n".join(lines)
