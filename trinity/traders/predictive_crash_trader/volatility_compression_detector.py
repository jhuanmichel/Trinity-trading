"""
volatility_compression_detector.py — Detector de Compressão de Volatilidade

Detecta setups de compressão que precedem breakouts explosivos:
  - Squeeze de Bollinger Bands (BB width < média histórica)
  - ATR comprimido (abaixo da média das últimas N velas)
  - Range percentual das últimas 24h abaixo do histórico
  - Acúmulo de liquidez em range estreito = spring loaded = risco de queda violenta

Lógica:
  bb_width        = (upper - lower) / middle × 100
  bb_squeeze      = bb_width < BB_SQUEEZE_THRESHOLD
  atr_compressed  = current_atr < atr_avg * ATR_COMPRESSION_RATIO
  range_tight     = range_24h_pct < RANGE_TIGHT_PCT

Após compressão, a expansão é geralmente:
  - DOWN se preço está caindo lentamente dentro do range (lower highs)
  - UP   se preço está subindo lentamente (higher lows)
  - UNKNOWN se indeterminado

breakout_probability [0-100]:
  - 100 → compressão extrema, liquidez acumulada, breakout iminente
  -   0 → volatilidade normal, sem sinais de compressão

Saída:
  {
    "breakout_probability":            0-100,
    "compression_detected":            bool,
    "bb_squeeze":                      bool,
    "atr_compressed":                  bool,
    "range_pct_24h":                   float,  # range % das últimas 24h
    "compression_duration_candles":    int,     # quantas velas em compressão
    "expected_direction":              "DOWN" | "UP" | "UNKNOWN",
    "bb_width_pct":                    float,
    "atr_ratio":                       float,  # current_atr / avg_atr
    "details":                         dict,
  }
"""
import logging
import math
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Limiares ──────────────────────────────────────────────────────────────────
BB_PERIOD               = 20      # períodos para BB
BB_STD_MULT             = 2.0     # multiplicador de desvio padrão
BB_SQUEEZE_THRESHOLD    = 2.5     # BB width < 2.5% = squeeze
ATR_PERIOD              = 14      # períodos para ATR
ATR_COMPRESSION_RATIO   = 0.65    # ATR atual < 65% da média = comprimido
RANGE_TIGHT_PCT         = 3.0     # range 24h < 3% = estreito
MIN_CANDLES_COMPRESSED  = 3       # mínimo 3 velas consecutivas com ATR baixo


def detect_volatility_compression(coin_data: dict) -> dict:
    """
    Detecta compressão de volatilidade a partir de klines.

    Args:
        coin_data: dict com "price", "high_24h", "low_24h", "klines_15m"

    Returns:
        dict com breakout_probability e detalhes
    """
    price     = float(coin_data.get("price", 0))
    high_24h  = float(coin_data.get("high_24h", price))
    low_24h   = float(coin_data.get("low_24h", price))
    klines    = coin_data.get("klines_15m", [])

    if not price:
        return _empty_result("Sem dados de preço")

    # ── Range 24h ─────────────────────────────────────────────────────────
    range_pct_24h = ((high_24h - low_24h) / low_24h * 100) if low_24h > 0 else 5.0

    if len(klines) < BB_PERIOD + 1:
        # Sem klines suficientes — usa range 24h apenas
        bb_squeeze = range_pct_24h < BB_SQUEEZE_THRESHOLD
        prob = _simple_probability(range_pct_24h, bb_squeeze)
        return {
            "breakout_probability":         round(prob, 1),
            "compression_detected":         bb_squeeze,
            "bb_squeeze":                   bb_squeeze,
            "atr_compressed":               False,
            "range_pct_24h":                round(range_pct_24h, 2),
            "compression_duration_candles": 0,
            "expected_direction":           "UNKNOWN",
            "bb_width_pct":                 range_pct_24h,
            "atr_ratio":                    1.0,
            "details":                      {"note": "Klines insuficientes — estimativa simplificada"},
        }

    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]

    # ── Bollinger Bands ────────────────────────────────────────────────────
    bb_width_pct, bb_squeeze = _calc_bollinger(closes)

    # ── ATR ───────────────────────────────────────────────────────────────
    atr_values = _calc_atr_series(highs, lows, closes)
    current_atr, avg_atr = _get_atr_stats(atr_values)
    atr_ratio    = current_atr / avg_atr if avg_atr > 0 else 1.0
    atr_compressed = atr_ratio < ATR_COMPRESSION_RATIO

    # ── Duração da compressão ─────────────────────────────────────────────
    compression_candles = _count_compression_duration(atr_values, avg_atr)

    # ── Direção esperada ──────────────────────────────────────────────────
    expected_direction = _detect_direction(closes, klines)

    # ── breakout_probability ──────────────────────────────────────────────
    compression_detected = bb_squeeze or atr_compressed
    prob = _calc_breakout_probability(
        bb_width_pct, atr_ratio, range_pct_24h,
        compression_candles, compression_detected,
    )

    log.debug(
        f"[VolComp] prob={prob:.0f} | bb_w={bb_width_pct:.2f}% "
        f"atr_r={atr_ratio:.2f} dur={compression_candles} "
        f"dir={expected_direction}"
    )

    return {
        "breakout_probability":         round(prob, 1),
        "compression_detected":         compression_detected,
        "bb_squeeze":                   bb_squeeze,
        "atr_compressed":               atr_compressed,
        "range_pct_24h":                round(range_pct_24h, 2),
        "compression_duration_candles": compression_candles,
        "expected_direction":           expected_direction,
        "bb_width_pct":                 round(bb_width_pct, 3),
        "atr_ratio":                    round(atr_ratio, 3),
        "details": {
            "current_atr":    round(current_atr, 4),
            "avg_atr":        round(avg_atr, 4),
            "bb_period":      BB_PERIOD,
            "atr_period":     ATR_PERIOD,
            "candles_used":   len(klines),
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc_bollinger(closes: List[float]) -> Tuple[float, bool]:
    """Calcula BB width e squeeze com os últimos BB_PERIOD fechamentos."""
    recent = closes[-BB_PERIOD:]
    if len(recent) < BB_PERIOD:
        return 5.0, False
    mean  = sum(recent) / len(recent)
    var   = sum((c - mean) ** 2 for c in recent) / len(recent)
    std   = math.sqrt(var)
    upper = mean + BB_STD_MULT * std
    lower = mean - BB_STD_MULT * std
    bb_width_pct = ((upper - lower) / mean * 100) if mean > 0 else 5.0
    return bb_width_pct, bb_width_pct < BB_SQUEEZE_THRESHOLD


def _calc_atr_series(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    """Calcula série de ATR para análise de compressão."""
    tr_values = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        c = closes[i - 1]
        tr = max(h - l, abs(h - c), abs(l - c))
        tr_values.append(tr)

    if not tr_values:
        return []

    # Suaviza com média móvel simples de ATR_PERIOD
    atr_series = []
    for i in range(len(tr_values)):
        start = max(0, i - ATR_PERIOD + 1)
        window = tr_values[start:i + 1]
        atr_series.append(sum(window) / len(window))

    return atr_series


def _get_atr_stats(atr_values: List[float]) -> Tuple[float, float]:
    """Retorna (current_atr, avg_atr) para os últimos valores."""
    if not atr_values:
        return 0.0, 0.0
    current_atr = atr_values[-1]
    # Média dos últimos 2×ATR_PERIOD para comparação histórica
    lookback = min(len(atr_values), ATR_PERIOD * 2)
    avg_atr  = sum(atr_values[-lookback:]) / lookback
    return current_atr, avg_atr


def _count_compression_duration(atr_values: List[float], avg_atr: float) -> int:
    """Conta quantas velas consecutivas estão com ATR abaixo do threshold."""
    if not atr_values or avg_atr == 0:
        return 0
    count = 0
    for atr in reversed(atr_values):
        if atr / avg_atr < ATR_COMPRESSION_RATIO * 1.2:  # 20% de tolerância
            count += 1
        else:
            break
    return count


def _detect_direction(closes: List[float], klines: list) -> str:
    """
    Tenta detectar direção esperada do breakout.
    Lower highs recentes → DOWN, higher lows → UP, ambíguo → UNKNOWN.
    """
    if len(closes) < 10:
        return "UNKNOWN"

    # Lower highs nos últimos 10 candles = bear bias
    highs_recent = [float(k[2]) for k in klines[-10:]]
    lows_recent  = [float(k[3]) for k in klines[-10:]]

    lower_highs = sum(
        1 for i in range(1, len(highs_recent))
        if highs_recent[i] < highs_recent[i - 1]
    )
    higher_lows = sum(
        1 for i in range(1, len(lows_recent))
        if lows_recent[i] > lows_recent[i - 1]
    )

    if lower_highs >= 6 and lower_highs > higher_lows:
        return "DOWN"
    if higher_lows >= 6 and higher_lows > lower_highs:
        return "UP"
    return "UNKNOWN"


def _calc_breakout_probability(
    bb_width: float,
    atr_ratio: float,
    range_24h: float,
    duration: int,
    compressed: bool,
) -> float:
    """Calcula breakout_probability [0-100]."""
    if not compressed:
        return max(0.0, (BB_SQUEEZE_THRESHOLD - bb_width) / BB_SQUEEZE_THRESHOLD * 40)

    score = 0.0

    # Fator 1: BB width (quanto mais estreito, maior) (0-35 pts)
    if bb_width < BB_SQUEEZE_THRESHOLD:
        score += min(35.0, (1 - bb_width / BB_SQUEEZE_THRESHOLD) * 35)

    # Fator 2: ATR comprimido (0-30 pts)
    if atr_ratio < ATR_COMPRESSION_RATIO:
        score += min(30.0, (1 - atr_ratio) * 30)

    # Fator 3: Range 24h estreito (0-20 pts)
    if range_24h < RANGE_TIGHT_PCT:
        score += min(20.0, (1 - range_24h / RANGE_TIGHT_PCT) * 20)

    # Fator 4: Duração da compressão (0-15 pts)
    score += min(15.0, duration * 3.0)

    return min(100.0, score)


def _simple_probability(range_pct: float, squeeze: bool) -> float:
    """Probabilidade simplificada quando não há klines."""
    if not squeeze:
        return 20.0
    return min(80.0, (BB_SQUEEZE_THRESHOLD - range_pct) / BB_SQUEEZE_THRESHOLD * 70 + 20)


def _empty_result(reason: str) -> dict:
    return {
        "breakout_probability":         0.0,
        "compression_detected":         False,
        "bb_squeeze":                   False,
        "atr_compressed":               False,
        "range_pct_24h":                5.0,
        "compression_duration_candles": 0,
        "expected_direction":           "UNKNOWN",
        "bb_width_pct":                 5.0,
        "atr_ratio":                    1.0,
        "details":                      {"error": reason},
    }
