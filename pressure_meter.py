"""
pressure_meter.py — Medidor de Pressão Institucional (IPM)
Capítulo 4 — Trinity Trading v1.0

Mede a intenção direcional dos participantes institucionais em tempo real.
Range de saída: -100 a +100
  +100 = pressão máxima bullish
  -100 = pressão máxima bearish

Filtro obrigatório (Cap. 7): |pressao| >= 40 para validar qualquer sinal

Pesos:
  liquidation_delta:  25%  — short_liq > long_liq = bullish
  volume_delta:       20%  — volume compra dominante
  market_structure:   20%  — BOS/CHoCH direção
  liquidity_position: 15%  — liquidez acima ou abaixo do preço
  htf_trend:          10%  — Diário/Semanal em alta/baixa
  oi_funding:         10%  — OI subindo, funding neutro
  TOTAL:             100%
"""

IPM_WEIGHTS = {
    "liquidation_delta":  25,
    "volume_delta":       20,
    "market_structure":   20,
    "liquidity_position": 15,
    "htf_trend":          10,
    "oi_funding":         10,
}

IPM_FILTER_THRESHOLD = 40   # |pressure| mínimo para sinal válido


# ─── Componentes individuais ────────────────────────────────────────────────

def _c_liquidation(liq_scoring: dict) -> float:
    """
    Short liquidations dominando → bullish (+)
    Long liquidations dominando  → bearish (-)
    Sem conexão WS               → 0 (neutro)
    """
    if not liq_scoring:
        return 0.0

    if not liq_scoring.get("connected", False):
        return 0.0  # sem dados de liquidação real → neutro

    bias     = liq_scoring.get("bias", "NEUTRAL")
    strength = float(liq_scoring.get("strength", 0))

    if bias == "LONG":    # shorts liquidados = bullish
        return min(100.0, strength)
    elif bias == "SHORT":  # longs liquidados = bearish
        return max(-100.0, -strength)
    return 0.0


def _c_volume(volume_data: dict) -> float:
    """
    Volume compra dominante → bullish (+)
    Volume venda dominante  → bearish (-)
    Score 50 = neutro (0)
    """
    if not volume_data:
        return 0.0

    bias  = volume_data.get("bias", "NEUTRO")
    score = float(volume_data.get("score", 50))
    normalized = (score - 50.0) * 2.0   # 50→0, 100→+100, 0→-100

    if bias == "LONG":
        return abs(normalized)
    elif bias == "SHORT":
        return -abs(normalized)
    return normalized


def _c_market_structure(inst_breakdown: dict) -> float:
    """
    BOS/CHoCH para cima → bullish (+)
    BOS/CHoCH para baixo → bearish (-)
    """
    if not inst_breakdown:
        return 0.0

    ms = inst_breakdown.get("market_structure", {})
    if not ms:
        return 0.0

    score = float(ms.get("score", 50))
    bias  = ms.get("bias", "NEUTRO")
    normalized = (score - 50.0) * 2.0

    if bias in ("LONG", "BULLISH"):
        return abs(normalized)
    elif bias in ("SHORT", "BEARISH"):
        return -abs(normalized)
    return normalized


def _c_liquidity_position(mm_data: dict, price: float) -> float:
    """
    Liquidez ACIMA do preço mais próxima → MM vai subir (+)
    Liquidez ABAIXO do preço mais próxima → MM vai descer (-)

    Lógica: pool mais próximo = alvo mais imediato do MM.
    """
    if not mm_data or not price or price <= 0:
        return 0.0

    liq_hi = mm_data.get("liquidity_target_high", 0.0)
    liq_lo = mm_data.get("liquidity_target_low", 0.0)

    if not liq_hi or not liq_lo:
        return 0.0

    dist_hi = abs(liq_hi - price) / price   # distância proporcional para cima
    dist_lo = abs(price - liq_lo) / price   # distância proporcional para baixo

    if dist_hi <= 0 or dist_lo <= 0:
        return 0.0

    # Quanto mais próxima a liquidez, maior o score
    if dist_hi < dist_lo:
        # Pool acima mais próximo → MM vai subir para buscar liquidez
        ratio = min(1.0, dist_lo / (dist_hi + 1e-9) - 1.0)
        return min(100.0, ratio * 50.0)
    elif dist_lo < dist_hi:
        # Pool abaixo mais próximo → MM vai descer
        ratio = min(1.0, dist_hi / (dist_lo + 1e-9) - 1.0)
        return max(-100.0, -ratio * 50.0)
    return 0.0


def _c_htf_trend(trend_data: dict) -> float:
    """
    Tendência HTF em alta → bullish (+)
    Tendência HTF em baixa → bearish (-)
    """
    if not trend_data:
        return 0.0

    score = float(trend_data.get("score", 50))
    bias  = trend_data.get("bias", "NEUTRO")
    normalized = (score - 50.0) * 2.0

    if bias in ("LONG", "BULLISH"):
        return abs(normalized)
    elif bias in ("SHORT", "BEARISH"):
        return -abs(normalized)
    return normalized


def _c_oi_funding(deriv_data: dict) -> float:
    """
    OI subindo + funding neutro → bullish (+)
    OI subindo + funding extremo → bearish (squeeze iminente)
    Score já calculado pelo derivatives.py.
    """
    if not deriv_data:
        return 0.0

    score = float(deriv_data.get("score", 50))
    bias  = deriv_data.get("bias", "NEUTRO")
    normalized = (score - 50.0) * 2.0

    if bias in ("LONG", "BULLISH"):
        return abs(normalized)
    elif bias in ("SHORT", "BEARISH"):
        return -abs(normalized)
    return normalized


# ─── Função principal ────────────────────────────────────────────────────────

def calculate_pressure(
    liq_scoring:    dict,
    volume_data:    dict,
    inst_breakdown: dict,
    mm_data:        dict,
    trend_data:     dict,
    deriv_data:     dict,
    price:          float,
) -> dict:
    """
    Calcula o Medidor de Pressão Institucional (IPM).

    Args:
        liq_scoring:    saída de btc_liquidation_engine.get_for_scoring()
        volume_data:    saída de volume.analyze(df)
        inst_breakdown: inst["breakdown"] de calculate_institutional_score()
        mm_data:        saída de run_market_maker_analysis()
        trend_data:     saída de trend.analyze(df)
        deriv_data:     saída de derivatives.analyze("BTC")
        price:          preço atual

    Returns:
        {
            "pressure":      float (-100 a +100),
            "direction":     "BULLISH" | "BEARISH" | "NEUTRAL",
            "filter_passed": bool (|pressure| >= 40),
            "components":    {nome: valor, ...}
        }
    """
    raw = {
        "liquidation_delta":  _c_liquidation(liq_scoring),
        "volume_delta":       _c_volume(volume_data),
        "market_structure":   _c_market_structure(inst_breakdown),
        "liquidity_position": _c_liquidity_position(mm_data, price),
        "htf_trend":          _c_htf_trend(trend_data),
        "oi_funding":         _c_oi_funding(deriv_data),
    }

    # Pressão ponderada (-100 a +100)
    total_w  = sum(IPM_WEIGHTS.values())  # = 100
    pressure = sum(raw[k] * (IPM_WEIGHTS[k] / total_w) for k in IPM_WEIGHTS)
    pressure = max(-100.0, min(100.0, pressure))

    if pressure >= IPM_FILTER_THRESHOLD:
        direction = "BULLISH"
    elif pressure <= -IPM_FILTER_THRESHOLD:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    return {
        "pressure":      round(pressure, 1),
        "direction":     direction,
        "filter_passed": abs(pressure) >= IPM_FILTER_THRESHOLD,
        "components":    {k: round(v, 1) for k, v in raw.items()},
    }
