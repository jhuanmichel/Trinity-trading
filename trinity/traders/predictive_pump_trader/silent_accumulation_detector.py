"""
silent_accumulation_detector.py — Detector de Acumulação Silenciosa (Gate.io)

Detecta acumulação institucional quieta ANTES do pump:
  - Funding rate levemente negativo → shorts pagando longs → institution comprando
  - OI crescendo sem pump de preço → posições longas sendo abertas silenciosamente
  - CVD positivo (compras dominando em klines) → pressure compradora oculta

Score 0-25:
  funding_score  (0-8)  → funding negativo = mercado pagando quem é long
  oi_score       (0-8)  → OI crescendo sem pump = acumulação quieta
  cvd_score      (0-9)  → CVD klines positivo = pressão compradora dominante

Saída:
  {
    "score":          0-25,    # score total de acumulação silenciosa
    "funding_score":  0-8,
    "oi_score":       0-8,
    "cvd_score":      0-9,
    "funding_rate":   float,   # funding rate atual
    "oi_change_pct":  float,   # variação % de OI
    "cvd_value":      float,   # CVD normalizado [-1, +1]
    "silent_acc":     bool,    # True se score >= SILENT_ACC_THRESH
  }
"""
import logging

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
FUNDING_NEG_THRESH  = -0.0001   # funding < -0.01%/8h = levemente negativo
FUNDING_NEG_STRONG  = -0.0004   # funding < -0.04%/8h = fortemente negativo
OI_GROWTH_QUIET     = 3.0       # OI cresce > 3% sem pump de preço = acumulação quieta
OI_GROWTH_STRONG    = 8.0       # OI cresce > 8% = acumulação forte
PRICE_FLAT_PCT      = 1.0       # preço variou < ±1% = "flat" (sem pump ainda)
CVD_POSITIVE_THRESH = 0.15      # CVD > 0.15 = compradores dominando
CVD_STRONG_THRESH   = 0.40      # CVD > 0.40 = pressão compradora forte
SILENT_ACC_THRESH   = 12        # score >= 12 = acumulação silenciosa detectada


def detect_silent_accumulation(coin_data: dict) -> dict:
    """
    Detecta acumulação institucional silenciosa.

    Args:
        coin_data: dict com keys "funding_rate", "oi_change_pct",
                   "price_change_pct", "klines_15m"

    Returns:
        dict com score (0-25) e detalhes
    """
    funding_rate = float(coin_data.get("funding_rate", 0.0))
    oi_change    = float(coin_data.get("oi_change_pct", 0.0))
    price_change = float(coin_data.get("price_change_pct", 0.0))
    klines       = coin_data.get("klines_15m", [])

    # ── Componente 1: Funding Score (0-8) ─────────────────────────────────
    f_score = _calc_funding_score(funding_rate)

    # ── Componente 2: OI Score (0-8) ──────────────────────────────────────
    # OI crescendo enquanto preço está flat = acumulação quieta
    oi_s = _calc_oi_score(oi_change, price_change)

    # ── Componente 3: CVD Score (0-9) ─────────────────────────────────────
    cvd_value = _calc_cvd_from_klines(klines)
    cvd_s     = _calc_cvd_score(cvd_value)

    total = round(f_score + oi_s + cvd_s, 2)
    total = min(25.0, total)

    silent_acc = total >= SILENT_ACC_THRESH

    log.debug(
        f"[SilentAcc] score={total:.1f}/25 "
        f"fund={f_score:.1f} oi={oi_s:.1f} cvd={cvd_s:.1f} "
        f"funding={funding_rate:.5f} oi_chg={oi_change:.1f}% cvd={cvd_value:.3f}"
    )

    return {
        "score":         total,
        "funding_score": round(f_score, 2),
        "oi_score":      round(oi_s, 2),
        "cvd_score":     round(cvd_s, 2),
        "funding_rate":  funding_rate,
        "oi_change_pct": oi_change,
        "cvd_value":     round(cvd_value, 4),
        "silent_acc":    silent_acc,
    }


# ── Cálculos internos ─────────────────────────────────────────────────────────

def _calc_funding_score(funding_rate: float) -> float:
    """
    Funding negativo = shorts pagando longs → institution comprando.

    0 pts → funding positivo ou zero
    4 pts → levemente negativo (< -0.01%/8h)
    8 pts → fortemente negativo (< -0.04%/8h)
    """
    if funding_rate >= 0:
        return 0.0
    if funding_rate <= FUNDING_NEG_STRONG:
        return 8.0
    # Interpolação linear entre 0 e -0.04%
    ratio = funding_rate / FUNDING_NEG_STRONG  # [0, 1]
    return round(ratio * 8.0, 2)


def _calc_oi_score(oi_change_pct: float, price_change_pct: float) -> float:
    """
    OI crescendo enquanto preço está flat = acumulação quieta.

    Condição chave: price_change < PRICE_FLAT_PCT (preço ainda não subiu)
    0 pts → OI caindo ou neutro
    4 pts → OI crescendo > 3% com preço flat
    8 pts → OI crescendo > 8% com preço flat
    """
    if oi_change_pct <= 0:
        return 0.0

    # Bônus reduzido se preço já subiu (acumulação deixou de ser "silenciosa")
    price_penalty = 1.0
    if abs(price_change_pct) > PRICE_FLAT_PCT:
        price_penalty = max(0.3, 1.0 - (abs(price_change_pct) - PRICE_FLAT_PCT) / 10.0)

    if oi_change_pct >= OI_GROWTH_STRONG:
        raw = 8.0
    elif oi_change_pct >= OI_GROWTH_QUIET:
        # Interpolação entre 3% e 8%
        raw = 4.0 + (oi_change_pct - OI_GROWTH_QUIET) / (OI_GROWTH_STRONG - OI_GROWTH_QUIET) * 4.0
    else:
        # Menos de 3%: contribuição proporcional (0-4 pts)
        raw = (oi_change_pct / OI_GROWTH_QUIET) * 4.0

    return round(min(8.0, raw * price_penalty), 2)


def _calc_cvd_score(cvd_value: float) -> float:
    """
    CVD positivo = compradores dominando volume.

    0 pts → CVD negativo (vendedores dominam)
    4 pts → CVD levemente positivo (> 0.15)
    9 pts → CVD fortemente positivo (> 0.40)
    """
    if cvd_value <= 0:
        return 0.0
    if cvd_value >= CVD_STRONG_THRESH:
        return 9.0
    if cvd_value >= CVD_POSITIVE_THRESH:
        # Interpolação entre 0.15 e 0.40
        ratio = (cvd_value - CVD_POSITIVE_THRESH) / (CVD_STRONG_THRESH - CVD_POSITIVE_THRESH)
        return round(4.0 + ratio * 5.0, 2)
    # CVD entre 0 e 0.15: contribuição mínima
    return round((cvd_value / CVD_POSITIVE_THRESH) * 4.0, 2)


def _calc_cvd_from_klines(klines: list) -> float:
    """
    CVD normalizado [-1, +1] a partir das últimas 20 velas 15m.

    Método: se close > open → bullish vol (+), se close <= open → bearish vol (-)
    CVD = (bull_vol - bear_vol) / total_vol
    """
    if not klines:
        return 0.0

    bull_vol = bear_vol = 0.0

    for k in klines[-20:]:
        try:
            o = float(k[1])
            c = float(k[4])
            v = float(k[5])
            if c > o:
                bull_vol += v
            else:
                bear_vol += v
        except (ValueError, TypeError, IndexError):
            continue

    total = bull_vol + bear_vol
    if total == 0:
        return 0.0

    return max(-1.0, min(1.0, (bull_vol - bear_vol) / total))
