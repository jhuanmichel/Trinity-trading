"""
macro_cycle_detector.py — Detector de Macro Ciclo
Trinity Trading | Bitcoin Cycle Engine

Agrega outputs de todos os sub-modelos para detectar o macro ciclo atual.
Usa regras compostas + score ponderado com sinais explícitos.
"""


def detect_macro_phase(
    trend_data:      dict,
    halving_data:    dict,
    onchain_data:    dict,
    volatility_data: dict,
) -> dict:
    """
    Detecta a fase macro do ciclo com base em todos os modelos.

    Returns:
        {
            "macro_phase":  str,   # fase detectada
            "macro_score":  float, # 0-100
            "bias":         "BULLISH" | "BEARISH" | "NEUTRAL",
            "signals":      list,  # sinais ativos
        }
    """
    signals = []
    score   = 50.0

    # ── Extrair inputs ────────────────────────────────────────────────────
    trend_dir   = trend_data.get("trend_direction", "SIDEWAYS")
    above_200w  = trend_data.get("above_200w_ma",   None)
    above_50w   = trend_data.get("above_50w_ma",    None)
    drawdown    = trend_data.get("drawdown_pct",    0.0)
    structure   = trend_data.get("macro_structure", "MIXED")

    hal_phase   = halving_data.get("halving_phase",     "MID")
    days_since  = halving_data.get("days_since_halving", 365)

    sm_behavior = onchain_data.get("smart_money_behavior", "NEUTRAL")
    mvrv        = onchain_data.get("mvrv",                 None)
    nupl_phase  = onchain_data.get("nupl_phase",           "OPTIMISM")

    vol_phase   = volatility_data.get("volatility_phase", "MEDIUM")

    # ── Sinais BULLISH ────────────────────────────────────────────────────
    if above_200w is True:
        score += 8;  signals.append("acima_200W_MA")

    if above_50w is True:
        score += 5;  signals.append("acima_50W_MA")

    if structure == "HH_HL":
        score += 8;  signals.append("estrutura_HH_HL")

    if trend_dir == "UP":
        score += 10; signals.append("tendencia_UP")

    if sm_behavior == "ACCUMULATING":
        score += 7;  signals.append("smart_money_acumulando")

    if vol_phase == "LOW" and trend_dir != "DOWN":
        score += 5;  signals.append("vol_baixa_compressao")

    if hal_phase in ("EARLY", "MID"):
        score += 8;  signals.append(f"halving_phase_{hal_phase}")

    if mvrv is not None and mvrv < 1.5:
        score += 5;  signals.append(f"mvrv_acumulacao_{mvrv:.2f}")

    # ── Sinais BEARISH ────────────────────────────────────────────────────
    if above_200w is False:
        score -= 8;  signals.append("abaixo_200W_MA")

    if structure == "LH_LL":
        score -= 8;  signals.append("estrutura_LH_LL")

    if trend_dir == "DOWN":
        score -= 10; signals.append("tendencia_DOWN")

    if sm_behavior == "DISTRIBUTING":
        score -= 7;  signals.append("smart_money_distribuindo")

    if vol_phase == "HIGH" and trend_dir != "UP":
        score -= 5;  signals.append("vol_alta_risco")

    if hal_phase == "BEAR":
        score -= 8;  signals.append("halving_phase_BEAR")

    if mvrv is not None and mvrv > 3.0:
        score -= 8;  signals.append(f"mvrv_distribuicao_{mvrv:.2f}")

    if nupl_phase == "EUPHORIA":
        score -= 6;  signals.append("nupl_euforia")

    if drawdown < -50:
        score -= 10; signals.append(f"drawdown_severo_{drawdown:.0f}pct")
    elif drawdown < -25:
        score -= 5;  signals.append(f"drawdown_moderado_{drawdown:.0f}pct")

    # ── Normaliza ─────────────────────────────────────────────────────────
    score = round(max(0.0, min(100.0, score)), 1)

    # Bias
    if   score >= 62: bias = "BULLISH"
    elif score <= 38: bias = "BEARISH"
    else:             bias = "NEUTRAL"

    # Fase macro simplificada para o classifier
    if hal_phase == "EARLY" and above_200w and trend_dir == "UP":
        macro_phase = "EARLY_BULL"
    elif hal_phase == "MID" and sm_behavior in ("ACCUMULATING", "NEUTRAL") and trend_dir != "DOWN":
        macro_phase = "MID_BULL"
    elif hal_phase == "LATE" or (mvrv is not None and mvrv > 3.0):
        macro_phase = "LATE_BULL"
    elif hal_phase == "BEAR" and trend_dir == "DOWN":
        macro_phase = "BEAR"
    elif not above_200w and vol_phase == "LOW" and sm_behavior == "ACCUMULATING":
        macro_phase = "ACCUMULATION"
    else:
        macro_phase = "NEUTRAL"

    return {
        "macro_phase":  macro_phase,
        "macro_score":  score,
        "bias":         bias,
        "signals":      signals,
    }
