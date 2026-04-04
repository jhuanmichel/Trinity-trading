"""
pump_prediction_model.py — Modelo de Predição de Pump (Cap. 20)

Detecta sinais de acumulação de smart money que precedem pumps:
  - Higher lows progressivos (estrutura de alta se formando)
  - Absorption: grande volume SEM movimentação de preço = acumulação
  - CVD positivo crescente = compra oculta (hidden buying)
  - Preço recuperando de queda com volume baixo = sem pressão vendedora
  - Bid absorption: bids absorvendo sells sem colapsar = mão forte comprando

Lógica:
  higher_lows_pattern    = 3+ higher lows nas últimas 10 velas
  absorption_detected    = volume spike SEM breakout de range
  hidden_buying          = CVD_crescente + preço flat ou subindo levemente
  smart_money_confidence = score 0-100

Saída:
  {
    "higher_lows_pattern":     bool,
    "absorption_detected":     bool,
    "hidden_buying":           bool,
    "smart_money_confidence":  0-100,
    "cvd_score":               float,    # +1 = todo volume comprando, -1 = vendendo
    "recovery_strength":       float,    # 0-100 quão forte é a recuperação
    "higher_lows_count":       int,
    "volume_absorption_ratio": float,
    "details":                 dict,
  }
"""
import logging

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
HIGHER_LOWS_MIN        = 3      # mínimo de higher lows para confirmar padrão
ABSORPTION_VOL_MULT    = 1.5    # volume do spike vs média (> 1.5x = spike)
ABSORPTION_RANGE_MAX   = 0.008  # range da vela < 0.8% durante spike = absorption
CVD_POSITIVE_THRESH    = 0.20   # CVD > 0.20 = compradores dominando
RECOVERY_DROP_MIN      = 3.0    # queda mínima anterior para avaliar recovery
RECOVERY_REBOUND_MIN   = 0.5    # rebound mínimo (50% da queda) = força
LOOKBACK_CANDLES       = 12     # janela de análise
VOLUME_LOOKBACK        = 20     # janela de volume para calcular média


def predict_pump(coin_data: dict) -> dict:
    klines       = coin_data.get("klines_15m", [])
    recent_trades = coin_data.get("recent_trades", [])
    price_change  = float(coin_data.get("price_change_pct", 0))

    if len(klines) < LOOKBACK_CANDLES + 5:
        return _empty_result("Klines insuficientes")

    try:
        opens   = [float(k[1]) for k in klines]
        highs   = [float(k[2]) for k in klines]
        lows    = [float(k[3]) for k in klines]
        closes  = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
    except (IndexError, ValueError, TypeError):
        return _empty_result("Klines inválidos")

    # ── Higher lows pattern ────────────────────────────────────────────────
    hl_count, higher_lows_pattern = _detect_higher_lows(lows)

    # ── Absorption ─────────────────────────────────────────────────────────
    absorption_detected, vol_absorption_ratio = _detect_absorption(
        opens, highs, lows, closes, volumes,
    )

    # ── CVD (Cumulative Volume Delta) a partir de klines ──────────────────
    cvd_score, hidden_buying = _calc_cvd(opens, closes, volumes)

    # ── Recovery strength ──────────────────────────────────────────────────
    recovery_strength = _calc_recovery_strength(
        closes, highs, lows, price_change,
    )

    # ── Smart money confidence ─────────────────────────────────────────────
    confidence = _calc_smart_money_confidence(
        higher_lows_pattern, hl_count,
        absorption_detected, vol_absorption_ratio,
        hidden_buying, cvd_score,
        recovery_strength,
    )

    log.debug(
        f"[PumpPred] conf={confidence:.0f} hl={hl_count} "
        f"absorb={absorption_detected} cvd={cvd_score:.3f} "
        f"hidden={hidden_buying} recovery={recovery_strength:.0f}"
    )

    return {
        "higher_lows_pattern":     higher_lows_pattern,
        "absorption_detected":     absorption_detected,
        "hidden_buying":           hidden_buying,
        "smart_money_confidence":  round(confidence, 1),
        "cvd_score":               round(cvd_score, 3),
        "recovery_strength":       round(recovery_strength, 1),
        "higher_lows_count":       hl_count,
        "volume_absorption_ratio": round(vol_absorption_ratio, 2),
        "details": {
            "price_change_pct":  price_change,
            "candles_analyzed":  min(len(klines), LOOKBACK_CANDLES),
        },
    }


# ── Cálculos internos ─────────────────────────────────────────────────────────

def _detect_higher_lows(lows: list, lookback: int = LOOKBACK_CANDLES) -> tuple:
    """
    Detecta higher lows progressivos.
    Retorna (count, is_pattern).
    """
    window = lows[-lookback:] if len(lows) >= lookback else lows
    count  = 0
    max_consecutive = 0

    for i in range(1, len(window)):
        if window[i] > window[i - 1]:
            count += 1
            max_consecutive = max(max_consecutive, count)
        else:
            count = 0

    return max_consecutive, max_consecutive >= HIGHER_LOWS_MIN


def _detect_absorption(
    opens: list, highs: list, lows: list, closes: list, volumes: list,
    lookback: int = LOOKBACK_CANDLES,
) -> tuple:
    """
    Detecta absorption: volume spike com range estreito.

    Sinal clássico de acumulação: muita negociação sem mover o preço
    = mão forte absorvendo vendas.

    Retorna (is_detected, volume_ratio).
    """
    if len(volumes) < VOLUME_LOOKBACK:
        return False, 1.0

    avg_vol    = sum(volumes[-VOLUME_LOOKBACK:-lookback]) / (VOLUME_LOOKBACK - lookback) \
                 if VOLUME_LOOKBACK > lookback else sum(volumes) / len(volumes)

    if avg_vol == 0:
        return False, 1.0

    # Analisa velas recentes
    window_opens   = opens[-lookback:]
    window_highs   = highs[-lookback:]
    window_lows    = lows[-lookback:]
    window_closes  = closes[-lookback:]
    window_volumes = volumes[-lookback:]

    absorption_candles = 0
    max_vol_ratio      = 1.0

    for i in range(len(window_volumes)):
        vol_ratio    = window_volumes[i] / avg_vol if avg_vol else 0
        mid_price    = (window_highs[i] + window_lows[i]) / 2
        candle_range = (window_highs[i] - window_lows[i]) / mid_price if mid_price else 0

        # Volume acima da média + range estreito = absorption
        if vol_ratio >= ABSORPTION_VOL_MULT and candle_range < ABSORPTION_RANGE_MAX:
            # Confirma: deve ser vela de compra (close > open) ou doji
            if window_closes[i] >= window_opens[i] * 0.998:
                absorption_candles += 1
                max_vol_ratio = max(max_vol_ratio, vol_ratio)

    return absorption_candles >= 2, max_vol_ratio


def _calc_cvd(opens: list, closes: list, volumes: list) -> tuple:
    """
    Calcula CVD normalizado a partir de klines.

    Método: se close > open → bullish (comprador ganhou), senão bearish.
    CVD = (bull_vol - bear_vol) / total_vol

    Retorna (cvd_score [-1, 1], is_hidden_buying).
    """
    window_opens   = opens[-LOOKBACK_CANDLES:]
    window_closes  = closes[-LOOKBACK_CANDLES:]
    window_volumes = volumes[-LOOKBACK_CANDLES:]

    bull_vol = 0.0
    bear_vol = 0.0

    for o, c, v in zip(window_opens, window_closes, window_volumes):
        if c >= o:
            bull_vol += v
        else:
            bear_vol += v

    total = bull_vol + bear_vol
    if total == 0:
        return 0.0, False

    cvd = (bull_vol - bear_vol) / total
    hidden_buying = cvd >= CVD_POSITIVE_THRESH

    return cvd, hidden_buying


def _calc_recovery_strength(
    closes: list, highs: list, lows: list, price_change_pct: float,
) -> float:
    """
    Avalia a força de recuperação: queda recente seguida de rebound.

    Score 0-100:
      - Queda significativa + rebound forte = alta probabilidade de pump
      - Preço já subindo após queda = momentum a favor
    """
    if len(closes) < 10:
        return 0.0

    # Queda recente: compara mínimo dos últimos 5 candles com máximo dos 5 anteriores
    recent_lows  = lows[-5:]
    prior_highs  = highs[-10:-5]

    if not prior_highs or not recent_lows:
        return 0.0

    prior_high = max(prior_highs)
    recent_low = min(recent_lows)
    current    = closes[-1]

    if prior_high == 0 or recent_low == 0:
        return 0.0

    # Queda que aconteceu
    drop_pct = (prior_high - recent_low) / prior_high * 100

    if drop_pct < RECOVERY_DROP_MIN:
        # Sem queda significativa — avalia apenas price_change positivo
        if price_change_pct > 0:
            return min(40.0, price_change_pct * 5)
        return 0.0

    # Rebound = quanto recuperou da queda
    rebound_pct = (current - recent_low) / (prior_high - recent_low) if (prior_high - recent_low) > 0 else 0

    if rebound_pct < RECOVERY_REBOUND_MIN:
        return min(30.0, rebound_pct * 60)

    # Recuperação forte: drop + rebound = coil spring
    score = min(60.0, drop_pct * 3) + min(40.0, rebound_pct * 40)
    return min(100.0, score)


def _calc_smart_money_confidence(
    higher_lows: bool, hl_count: int,
    absorption: bool, vol_ratio: float,
    hidden_buying: bool, cvd: float,
    recovery: float,
) -> float:
    score = 0.0

    # Fator 1: Higher lows (0-30 pts)
    if higher_lows:
        score += min(30.0, hl_count * 7)

    # Fator 2: Absorption (0-25 pts)
    if absorption:
        score += min(25.0, vol_ratio * 8)

    # Fator 3: CVD positivo / hidden buying (0-25 pts)
    if hidden_buying:
        score += min(25.0, (cvd / 1.0) * 25)
    elif cvd > 0:
        score += min(10.0, cvd * 20)

    # Fator 4: Recovery strength (0-20 pts)
    score += recovery * 0.20

    return min(100.0, score)


def _empty_result(reason: str) -> dict:
    return {
        "higher_lows_pattern":     False,
        "absorption_detected":     False,
        "hidden_buying":           False,
        "smart_money_confidence":  0.0,
        "cvd_score":               0.0,
        "recovery_strength":       0.0,
        "higher_lows_count":       0,
        "volume_absorption_ratio": 1.0,
        "details":                 {"error": reason},
    }
