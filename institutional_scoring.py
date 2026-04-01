"""
institutional_scoring.py — CAMADA 7: Score Institucional Multi-Camadas
Sistema de pontuação de 100 pontos distribuídos em 6 camadas:
  Market Structure → 20 pts
  Liquidity        → 20 pts  (derivatives + liquidations)
  Volume           → 20 pts
  Trend            → 20 pts
  Correlation      → 10 pts
  Volatility       → 10 pts

Resultado:
  80–100 → EXTREMAMENTE FORTE
  60–80  → FORTE
  40–60  → MODERADO
  < 40   → NEUTRO / SEM SINAL
"""

INST_WEIGHTS = {
    "market_structure": 20,
    "liquidity":        20,   # derivativos + liquidações combinados
    "volume":           20,
    "trend":            20,
    "correlation":      10,
    "volatility":       10,
}


def _liquidity_score(derivatives_data: dict, liquidations_data: dict) -> int:
    """Combina derivatives + liquidações em um score único de liquidez."""
    d_score = derivatives_data.get("score", 50)
    l_score = liquidations_data.get("score", 50)
    return round((d_score + l_score) / 2)


def _volatility_score(regime_data: dict) -> int:
    """
    Calcula score de volatilidade baseado em ATR + BB Squeeze.
    Compressão (squeeze) + direção = sinal de explosão.
    """
    score     = 50
    squeeze   = regime_data.get("squeeze", False)
    direction = regime_data.get("direction", "ALTA")
    adx       = regime_data.get("adx", 20)
    atr_pct   = regime_data.get("atr_pct", 1.0)

    if squeeze:
        # BB squeeze = energia acumulada; direção define o bias
        score = 70 if direction == "ALTA" else 30
    else:
        # Sem squeeze: usa direção do ADX
        if adx >= 25:
            score = 65 if direction == "ALTA" else 35
        elif adx >= 20:
            score = 58 if direction == "ALTA" else 42

    # ATR alto = momentum forte (confirma)
    if atr_pct > 2.5:
        score = min(100, score + 5) if score > 50 else max(0, score - 5)

    return max(0, min(100, int(score)))


def calculate_institutional_score(
    market_structure_data: dict,
    derivatives_data: dict,
    liquidations_data: dict,
    volume_data: dict,
    trend_data: dict,
    correlation_data: dict,
    regime_data: dict,
) -> dict:
    """
    Calcula o score institucional final (0-100) e classifica o sinal.

    Returns:
        dict com inst_score, signal, confidence, confluences, breakdown, summary
    """
    liq_score  = _liquidity_score(derivatives_data, liquidations_data)
    volt_score = _volatility_score(regime_data)

    layer_scores = {
        "market_structure": market_structure_data.get("score", 50),
        "liquidity":        liq_score,
        "volume":           volume_data.get("score", 50),
        "trend":            trend_data.get("score", 50),
        "correlation":      correlation_data.get("score", 50),
        "volatility":       volt_score,
    }

    # Weighted sum
    total_weight = sum(INST_WEIGHTS.values())
    weighted_sum = sum(
        layer_scores[layer] * INST_WEIGHTS[layer]
        for layer in INST_WEIGHTS
    )
    inst_score = round(weighted_sum / total_weight, 1)

    # Confluências: quantas camadas apontam na mesma direção que o score
    direction  = "LONG" if inst_score > 50 else "SHORT"
    confluences = sum(
        1 for s in layer_scores.values()
        if (s > 55 and direction == "LONG") or (s < 45 and direction == "SHORT")
    )

    # Invincible Mode: só válido com ≥ 3 confluências
    valid = confluences >= 3

    signal, confidence, strength = _classify(inst_score, confluences, valid)

    # Breakdown por camada
    breakdown = {
        layer: {
            "score":        layer_scores[layer],
            "weight":       INST_WEIGHTS[layer],
            "contribution": round(layer_scores[layer] * INST_WEIGHTS[layer] / total_weight, 1),
            "bias":         "LONG" if layer_scores[layer] > 55 else "SHORT" if layer_scores[layer] < 45 else "NEUTRO",
        }
        for layer in INST_WEIGHTS
    }

    return {
        "inst_score":   inst_score,
        "signal":       signal,
        "confidence":   confidence,
        "strength":     strength,
        "confluences":  confluences,
        "valid":        valid,
        "direction":    direction if valid else "AGUARDANDO",
        "layer_scores": layer_scores,
        "breakdown":    breakdown,
        "summary":      _format_summary(inst_score, signal, confidence, confluences, valid, breakdown),
    }


def _classify(score: float, confluences: int, valid: bool) -> tuple:
    """Retorna (signal, confidence, strength)."""
    if not valid:
        return "NO SIGNAL", f"apenas {confluences}/6 camadas alinhadas", "INSUFICIENTE"

    if score >= 80:
        return "LONG EXTREMAMENTE FORTE", f"{confluences}/6 camadas", "EXTREMO"
    elif score >= 70:
        return "LONG FORTE",              f"{confluences}/6 camadas", "FORTE"
    elif score >= 60:
        return "LONG MODERADO",           f"{confluences}/6 camadas", "MODERADO"
    elif score <= 20:
        return "SHORT EXTREMAMENTE FORTE", f"{confluences}/6 camadas", "EXTREMO"
    elif score <= 30:
        return "SHORT FORTE",              f"{confluences}/6 camadas", "FORTE"
    elif score <= 40:
        return "SHORT MODERADO",           f"{confluences}/6 camadas", "MODERADO"
    else:
        return "NO SIGNAL", "zona indefinida", "NEUTRO"


def _format_summary(score, signal, confidence, confluences, valid, breakdown) -> str:
    bar = "█" * int(score / 5) + "░" * (20 - int(score / 5))
    lines = [
        "═" * 52,
        f"  SCORE INSTITUCIONAL: {score}/100",
        f"  [{bar}]",
        f"  SINAL: {signal}",
        f"  Confluências: {confluences}/6 {'✅ VÁLIDO' if valid else '❌ INSUFICIENTE'}",
        "─" * 52,
        "  CAMADAS:",
    ]
    for layer, data in breakdown.items():
        b = "█" * int(data["score"] / 10) + "░" * (10 - int(data["score"] / 10))
        lines.append(
            f"  {layer:<18} [{b}] {data['score']:3d}  ({data['bias']})"
        )
    lines.append("═" * 52)
    return "\n".join(lines)
