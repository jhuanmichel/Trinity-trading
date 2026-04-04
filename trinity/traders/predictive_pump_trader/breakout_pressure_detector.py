"""
breakout_pressure_detector.py — Detector de Pressão de Breakout (Cap. 20)

Detecta condições de compressão + pressão acumulada que precedem breakouts de alta:
  - Volatilidade comprimida (Bollinger Bands estreitas, ATR baixo)
  - Volume crescendo durante compressão = acumulação silenciosa
  - Highers lows = estrutura de alta se formando
  - Range tightening = mola sendo comprimida

Lógica:
  compression_detected = BB_width < 2.5% AND atr_ratio < 0.65
  volume_building       = volume recente > média anterior em +20%
  higher_lows           = mínimos crescentes nas últimas N velas
  breakout_up           = compression + volume_building + higher_lows

breakout_probability [0-100]:
  - 100 → compressão extrema + volume acumulando + higher lows = pump iminente
  -   0 → sem compressão, volatilidade normal

Saída:
  {
    "compression_detected":        bool,
    "breakout_probability":        0-100,
    "breakout_direction":          "UP" | "DOWN" | "NEUTRAL",
    "volume_building":             bool,
    "higher_lows":                 bool,
    "bb_width_pct":                float,
    "atr_ratio":                   float,
    "volume_ratio":                float,   # vol_recente / vol_media
    "compression_duration_candles": int,
    "details":                     dict,
  }
"""
import logging

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BB_WINDOW          = 20      # janela das Bollinger Bands
BB_STD             = 2.0     # desvios padrão
BB_SQUEEZE_THRESH  = 0.025   # BB_width < 2.5% = compressão
ATR_WINDOW         = 14      # janela ATR
ATR_COMPRESSION    = 0.65    # ATR atual < 65% da média = comprimido
ATR_STRONG_COMP    = 0.45    # < 45% = compressão forte
VOLUME_BUILD_RATIO = 1.20    # volume recente > 120% da média = acumulando
HIGHER_LOWS_MIN    = 3       # mínimo de higher lows consecutivos para confirmar
RECENT_CANDLES     = 6       # velas recentes para análise de volume


def detect_breakout_pressure(coin_data: dict) -> dict:
    klines = coin_data.get("klines_15m", [])

    if len(klines) < BB_WINDOW + 5:
        return _empty_result("Klines insuficientes")

    try:
        closes  = [float(k[4]) for k in klines]
        highs   = [float(k[2]) for k in klines]
        lows    = [float(k[3]) for k in klines]
        volumes = [float(k[5]) for k in klines]
    except (IndexError, ValueError, TypeError):
        return _empty_result("Klines inválidos")

    # ── Bollinger Bands ────────────────────────────────────────────────────
    bb_width_pct, bb_squeeze = _calc_bb_squeeze(closes)

    # ── ATR ───────────────────────────────────────────────────────────────
    atr_ratio, atr_compressed = _calc_atr_ratio(highs, lows, closes)

    compression_detected = bb_squeeze and atr_compressed

    # ── Volume building ────────────────────────────────────────────────────
    vol_ratio, volume_building = _calc_volume_building(volumes)

    # ── Higher lows ────────────────────────────────────────────────────────
    higher_lows_count = _count_higher_lows(lows)
    higher_lows       = higher_lows_count >= HIGHER_LOWS_MIN

    # ── Lower highs (bearish — para identificar direção) ───────────────────
    lower_highs_count = _count_lower_highs(highs)
    lower_highs       = lower_highs_count >= HIGHER_LOWS_MIN

    # ── Duração da compressão ──────────────────────────────────────────────
    duration = _compression_duration(closes, highs, lows)

    # ── Direção esperada ──────────────────────────────────────────────────
    if higher_lows and not lower_highs:
        direction = "UP"
    elif lower_highs and not higher_lows:
        direction = "DOWN"
    elif compression_detected:
        # Sem padrão claro — usa volume como desempate
        direction = "UP" if volume_building else "NEUTRAL"
    else:
        direction = "NEUTRAL"

    # ── Probabilidade de breakout ──────────────────────────────────────────
    prob = _calc_breakout_probability(
        compression_detected, bb_width_pct, atr_ratio,
        volume_building, vol_ratio, higher_lows, higher_lows_count, duration,
        direction,
    )

    log.debug(
        f"[BreakoutP] prob={prob:.0f} comp={compression_detected} "
        f"bb={bb_width_pct*100:.2f}% atr_r={atr_ratio:.2f} "
        f"vol_r={vol_ratio:.2f} hl={higher_lows_count} dir={direction}"
    )

    return {
        "compression_detected":         compression_detected,
        "breakout_probability":         round(prob, 1),
        "breakout_direction":           direction,
        "volume_building":              volume_building,
        "higher_lows":                  higher_lows,
        "bb_width_pct":                 round(bb_width_pct * 100, 3),
        "atr_ratio":                    round(atr_ratio, 3),
        "volume_ratio":                 round(vol_ratio, 2),
        "compression_duration_candles": duration,
        "details": {
            "higher_lows_count":  higher_lows_count,
            "lower_highs_count":  lower_highs_count,
            "bb_squeeze":         bb_squeeze,
            "atr_compressed":     atr_compressed,
        },
    }


# ── Cálculos internos ─────────────────────────────────────────────────────────

def _calc_bb_squeeze(closes: list) -> tuple:
    """Retorna (bb_width_pct, is_squeeze)."""
    if len(closes) < BB_WINDOW:
        return 0.05, False

    window = closes[-BB_WINDOW:]
    mean   = sum(window) / BB_WINDOW
    if mean == 0:
        return 0.05, False

    variance = sum((x - mean) ** 2 for x in window) / BB_WINDOW
    std      = variance ** 0.5

    upper = mean + BB_STD * std
    lower = mean - BB_STD * std
    width = (upper - lower) / mean if mean else 0.0

    return width, width < BB_SQUEEZE_THRESH


def _calc_atr_ratio(highs: list, lows: list, closes: list) -> tuple:
    """Retorna (atr_ratio, is_compressed). ratio = ATR_atual / ATR_média."""
    if len(closes) < ATR_WINDOW + 1:
        return 1.0, False

    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    if len(trs) < ATR_WINDOW:
        return 1.0, False

    current_atr = sum(trs[-ATR_WINDOW:]) / ATR_WINDOW
    avg_atr     = sum(trs) / len(trs)

    if avg_atr == 0:
        return 1.0, False

    ratio = current_atr / avg_atr
    return ratio, ratio < ATR_COMPRESSION


def _calc_volume_building(volumes: list) -> tuple:
    """Retorna (vol_ratio, is_building). Compara últimas N velas com média anterior."""
    if len(volumes) < RECENT_CANDLES * 2:
        return 1.0, False

    recent = volumes[-RECENT_CANDLES:]
    prior  = volumes[-(RECENT_CANDLES * 3):-RECENT_CANDLES]

    avg_recent = sum(recent) / len(recent)
    avg_prior  = sum(prior) / len(prior) if prior else 0

    if avg_prior == 0:
        return 1.0, False

    ratio = avg_recent / avg_prior
    return ratio, ratio >= VOLUME_BUILD_RATIO


def _count_higher_lows(lows: list, lookback: int = 10) -> int:
    """Conta quantos higher lows consecutivos existem nas últimas N velas."""
    window = lows[-lookback:] if len(lows) >= lookback else lows
    count  = 0
    for i in range(1, len(window)):
        if window[i] > window[i - 1]:
            count += 1
        else:
            count = 0  # reset — queremos consecutivos
    return count


def _count_lower_highs(highs: list, lookback: int = 10) -> int:
    """Conta lower highs consecutivos."""
    window = highs[-lookback:] if len(highs) >= lookback else highs
    count  = 0
    for i in range(1, len(window)):
        if window[i] < window[i - 1]:
            count += 1
        else:
            count = 0
    return count


def _compression_duration(closes: list, highs: list, lows: list) -> int:
    """Estima há quantas velas o range está comprimido."""
    if len(closes) < 5:
        return 0

    price = closes[-1]
    if price == 0:
        return 0

    # Calcula range de cada vela relativo ao preço
    count = 0
    for i in range(len(closes) - 1, -1, -1):
        candle_range = (highs[i] - lows[i]) / closes[i] if closes[i] else 0
        if candle_range < 0.015:  # range < 1.5% = compressão
            count += 1
        else:
            break

    return count


def _calc_breakout_probability(
    compression: bool, bb_width: float, atr_ratio: float,
    vol_building: bool, vol_ratio: float,
    higher_lows: bool, hl_count: int, duration: int,
    direction: str,
) -> float:
    score = 0.0

    # Fator 1: Compressão (0-35 pts)
    if compression:
        score += 20.0
        # Bônus por compressão forte
        if bb_width < 0.015:
            score += 10.0
        if atr_ratio < ATR_STRONG_COMP:
            score += 5.0

    # Fator 2: Volume acumulando (0-25 pts)
    if vol_building:
        score += min(25.0, (vol_ratio - 1.0) * 25)

    # Fator 3: Higher lows (0-25 pts) — estrutura bullish
    if higher_lows:
        score += min(25.0, hl_count * 5)

    # Fator 4: Duração da compressão (0-15 pts) — mola mais comprimida
    score += min(15.0, duration * 2)

    # Penalidade: breakout para baixo reduz probabilidade de pump
    if direction == "DOWN":
        score *= 0.5

    return min(100.0, score)


def _empty_result(reason: str) -> dict:
    return {
        "compression_detected":         False,
        "breakout_probability":         0.0,
        "breakout_direction":           "NEUTRAL",
        "volume_building":              False,
        "higher_lows":                  False,
        "bb_width_pct":                 0.0,
        "atr_ratio":                    1.0,
        "volume_ratio":                 1.0,
        "compression_duration_candles": 0,
        "details":                      {"error": reason},
    }
