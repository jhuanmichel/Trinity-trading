"""
cycle_classifier.py — Classificador de Fase do Ciclo Bitcoin
Trinity Trading | Bitcoin Cycle Engine

CyclePhase:
  ACCUMULATION  — abaixo/próximo da 200W MA, vol baixa, smart money acumulando
  EARLY_BULL    — rompimento acima 200W MA, HLs, volume crescendo
  MID_BULL      — tendência forte, momentum, acumulação institucional
  LATE_BULL     — parabólico, MVRV elevado, euforia retail
  DISTRIBUTION  — lateral no topo, vol expandindo, smart money vendendo
  BEAR          — lower highs, downtrend confirmado
  CAPITULATION  — queda em pânico, medo extremo, cascata de liquidações
"""

from enum import Enum


# ─── Enums e Constantes ───────────────────────────────────────────────────────

class CyclePhase(Enum):
    ACCUMULATION = "ACCUMULATION"
    EARLY_BULL   = "EARLY_BULL"
    MID_BULL     = "MID_BULL"
    LATE_BULL    = "LATE_BULL"
    DISTRIBUTION = "DISTRIBUTION"
    BEAR         = "BEAR"
    CAPITULATION = "CAPITULATION"


# Multiplicadores de risco por fase (ajuste de tamanho de posição)
RISK_MULTIPLIERS = {
    CyclePhase.ACCUMULATION: 1.0,    # neutro, esperando confirmação
    CyclePhase.EARLY_BULL:   1.2,    # mais agressivo → melhor R/R histórico
    CyclePhase.MID_BULL:     1.4,    # pico de apetite de risco
    CyclePhase.LATE_BULL:    0.6,    # reduz exposição gradualmente
    CyclePhase.DISTRIBUTION: 0.5,    # risco elevado, smart money saindo
    CyclePhase.BEAR:         0.8,    # só swings curtos / hedge
    CyclePhase.CAPITULATION: 0.5,    # evitar; apenas scalp extremo
}

# Descrições para output e alertas
PHASE_DESCRIPTIONS = {
    CyclePhase.ACCUMULATION: "Acumulacao — smart money comprando abaixo do custo",
    CyclePhase.EARLY_BULL:   "Early Bull — rompimento confirmado, melhor R/R",
    CyclePhase.MID_BULL:     "Mid Bull — tendencia forte, momentum saudavel",
    CyclePhase.LATE_BULL:    "Late Bull — parabolico, reduzir exposicao gradualmente",
    CyclePhase.DISTRIBUTION: "Distribuicao — smart money vendendo no topo",
    CyclePhase.BEAR:         "Bear Market — downtrend confirmado, proteja capital",
    CyclePhase.CAPITULATION: "Capitulacao — panico extremo, aguardar reversao",
}


# ─── Classifier ───────────────────────────────────────────────────────────────

def classify_cycle_phase(
    trend_data:      dict,
    halving_data:    dict,
    onchain_data:    dict,
    volatility_data: dict,
    macro_data:      dict,
) -> CyclePhase:
    """
    Classifica a fase do ciclo Bitcoin usando regras compostas.
    Ordem: mais específico → mais genérico.

    Returns:
        CyclePhase enum
    """
    # Extrair inputs
    trend_dir  = trend_data.get("trend_direction",  "SIDEWAYS")
    above_200w = trend_data.get("above_200w_ma",    None)
    above_50w  = trend_data.get("above_50w_ma",     None)
    drawdown   = trend_data.get("drawdown_pct",     0.0)
    structure  = trend_data.get("macro_structure",  "MIXED")

    hal_phase  = halving_data.get("halving_phase",     "MID")
    cycle_age  = halving_data.get("cycle_age_pct",     50.0)

    sm_beh     = onchain_data.get("smart_money_behavior", "NEUTRAL")
    mvrv       = onchain_data.get("mvrv",                 None)
    nupl_phase = onchain_data.get("nupl_phase",           "OPTIMISM")

    vol_phase  = volatility_data.get("volatility_phase", "MEDIUM")
    atr_expand = volatility_data.get("atr_expansion",    False)

    macro_ph   = macro_data.get("macro_phase",  "NEUTRAL")
    macro_score= macro_data.get("macro_score",  50.0)

    # ── CAPITULATION ─────────────────────────────────────────────────────
    # Queda severa + vol alta + downtrend + smart money comprando
    if (drawdown < -60 and vol_phase == "HIGH"
            and trend_dir == "DOWN" and sm_beh == "ACCUMULATING"):
        return CyclePhase.CAPITULATION

    if nupl_phase == "CAPITULATION":
        return CyclePhase.CAPITULATION

    if mvrv is not None and mvrv < 0.7 and trend_dir == "DOWN":
        return CyclePhase.CAPITULATION

    # ── BEAR ──────────────────────────────────────────────────────────────
    # Downtrend confirmado, abaixo 200W MA, estrutura LH/LL
    if (trend_dir == "DOWN"
            and above_200w is not None and not above_200w
            and structure == "LH_LL"):
        return CyclePhase.BEAR

    if (hal_phase == "BEAR" and trend_dir == "DOWN"
            and sm_beh != "ACCUMULATING"):
        return CyclePhase.BEAR

    # ── DISTRIBUTION ──────────────────────────────────────────────────────
    # No topo, vol expandindo, smart money saindo
    if (sm_beh == "DISTRIBUTING"
            and vol_phase in ("MEDIUM", "HIGH")
            and trend_dir in ("UP", "SIDEWAYS")
            and above_50w is True):
        return CyclePhase.DISTRIBUTION

    if nupl_phase == "EUPHORIA" or (mvrv is not None and mvrv > 3.5 and atr_expand):
        return CyclePhase.DISTRIBUTION

    # ── LATE_BULL ─────────────────────────────────────────────────────────
    # Tendência up mas MVRV elevado ou fase LATE do halving
    if (trend_dir == "UP" and above_200w is True
            and ((mvrv is not None and mvrv > 2.8)
                 or hal_phase == "LATE"
                 or nupl_phase in ("BELIEF", "EUPHORIA"))):
        return CyclePhase.LATE_BULL

    # ── MID_BULL ──────────────────────────────────────────────────────────
    # Tendência forte, acima 200W e 50W, halving mid, MVRV saudável
    if (trend_dir == "UP"
            and above_200w is True and above_50w is True
            and sm_beh in ("ACCUMULATING", "NEUTRAL")
            and hal_phase in ("EARLY", "MID")
            and (mvrv is None or 1.5 < mvrv <= 2.8)):
        return CyclePhase.MID_BULL

    # ── EARLY_BULL ────────────────────────────────────────────────────────
    # Acima 200W MA, HLs se formando, smart money acumulando/neutro
    if (above_200w is True
            and hal_phase in ("ACCUMULATION", "EARLY")
            and trend_dir in ("UP", "SIDEWAYS")
            and sm_beh in ("ACCUMULATING", "NEUTRAL")):
        return CyclePhase.EARLY_BULL

    # ── ACCUMULATION ──────────────────────────────────────────────────────
    # Abaixo/próximo 200W MA, vol baixa, smart money acumulando
    if (vol_phase == "LOW"
            and sm_beh == "ACCUMULATING"
            and (above_200w is False
                 or (above_200w is True and trend_dir == "SIDEWAYS"))):
        return CyclePhase.ACCUMULATION

    if (hal_phase == "ACCUMULATION"
            and sm_beh == "ACCUMULATING"
            and trend_dir != "DOWN"):
        return CyclePhase.ACCUMULATION

    # ── Fallback via macro_phase ──────────────────────────────────────────
    _phase_map = {
        "EARLY_BULL":   CyclePhase.EARLY_BULL,
        "MID_BULL":     CyclePhase.MID_BULL,
        "LATE_BULL":    CyclePhase.LATE_BULL,
        "BEAR":         CyclePhase.BEAR,
        "ACCUMULATION": CyclePhase.ACCUMULATION,
    }
    if macro_ph in _phase_map:
        return _phase_map[macro_ph]

    # Default final: se acima 200W → EARLY_BULL, senão ACCUMULATION
    return CyclePhase.EARLY_BULL if above_200w else CyclePhase.ACCUMULATION
