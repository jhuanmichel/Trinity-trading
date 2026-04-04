"""
short_squeeze_detector.py — Detector de Short Squeeze

Detecta condições que precedem squeezes violentos de shorts:
  - Long/Short ratio baixo (shorts dominando = mercado sobrevendido)
  - Funding negativo extremo (shorts pagando longs → insustentável)
  - OI crescendo com funding negativo = shorts acumulando = armadilha
  - Preço segurando ou subindo levemente com muitos shorts abertos

Lógica:
  short_heavy      = l/s ratio < SHORT_HEAVY_RATIO (ex: 0.70 = 70% shorts)
  funding_negative = funding < FUNDING_NEGATIVE_THRESH (ex: -0.04%)
  oi_growing       = oi_change > OI_GROWING_PCT
  squeeze_setup    = short_heavy AND (funding_negative OR oi_growing)

squeeze_probability [0-100]:
  - 100 → shorts extremamente sobrecarregados, squeeze iminente
  -   0 → mercado equilibrado / longs dominando (sem squeeze fuel)

Saída:
  {
    "short_squeeze_risk":    bool,
    "squeeze_probability":   0-100,
    "short_heavy":           bool,
    "funding_negative":      bool,
    "oi_growing":            bool,
    "funding_rate":          float,
    "long_short_ratio":      float,
    "oi_change_pct":         float,
    "funding_annualized":    float,
    "shorts_trapped":        bool,   # OI alto + funding negativo = shorts presos
    "details":               dict,
  }
"""
import logging

log = logging.getLogger(__name__)

SHORT_HEAVY_RATIO      = 0.70   # l/s < 0.70 = muitos shorts
SHORT_EXTREME_RATIO    = 0.50   # l/s < 0.50 = shorts extremos
FUNDING_NEGATIVE_THRESH = -0.0003  # < -0.03% = shorts pagando muito
FUNDING_EXTREME_NEG    = -0.0006   # < -0.06% = extremo
OI_GROWING_PCT         = 5.0    # OI cresceu > 5% = shorts aumentando
PRICE_HOLDING_PCT      = 1.0    # preço caiu < 1% mesmo com muitos shorts = suporte
FUNDING_8H_PER_YEAR    = 3 * 365


def detect_short_squeeze(coin_data: dict) -> dict:
    price            = float(coin_data.get("price", 0))
    price_change_pct = float(coin_data.get("price_change_pct", 0))
    funding_rate     = float(coin_data.get("funding_rate", 0))
    oi_change_pct    = float(coin_data.get("oi_change_pct", 0))
    long_short_ratio = float(coin_data.get("long_short_ratio", 1.0))
    open_interest    = float(coin_data.get("open_interest", 0))

    if not price:
        return _empty_result("Sem preço")

    short_heavy      = long_short_ratio < SHORT_HEAVY_RATIO
    funding_negative = funding_rate < FUNDING_NEGATIVE_THRESH
    oi_growing       = oi_change_pct >= OI_GROWING_PCT
    shorts_trapped   = (
        long_short_ratio < SHORT_HEAVY_RATIO
        and funding_rate < FUNDING_NEGATIVE_THRESH
        and oi_change_pct >= 3.0
    )

    short_squeeze_risk = short_heavy or (funding_negative and oi_growing)
    funding_annualized = funding_rate * FUNDING_8H_PER_YEAR * 100

    prob = _calc_squeeze_probability(
        funding_rate, long_short_ratio, oi_change_pct,
        price_change_pct, shorts_trapped,
    )

    log.debug(
        f"[ShortSq] prob={prob:.0f} l/s={long_short_ratio:.2f} "
        f"fund={funding_rate:.4f} oi_chg={oi_change_pct:.1f}% trapped={shorts_trapped}"
    )

    return {
        "short_squeeze_risk":  short_squeeze_risk,
        "squeeze_probability": round(prob, 1),
        "short_heavy":         short_heavy,
        "funding_negative":    funding_negative,
        "oi_growing":          oi_growing,
        "funding_rate":        funding_rate,
        "long_short_ratio":    round(long_short_ratio, 2),
        "oi_change_pct":       round(oi_change_pct, 2),
        "funding_annualized":  round(funding_annualized, 1),
        "shorts_trapped":      shorts_trapped,
        "details": {
            "open_interest_usd": round(open_interest),
            "price_change_pct":  round(price_change_pct, 2),
            "funding_per_8h":    f"{funding_rate * 100:.4f}%",
        },
    }


def _calc_squeeze_probability(
    funding: float, ls_ratio: float, oi_chg: float,
    price_chg: float, trapped: bool,
) -> float:
    score = 0.0

    # Fator 1: Funding negativo (0-35 pts) — shorts pagando = insustentável
    if funding < FUNDING_EXTREME_NEG:
        score += 35.0
    elif funding < FUNDING_NEGATIVE_THRESH:
        score += min(35.0, abs(funding) / abs(FUNDING_EXTREME_NEG) * 25)

    # Fator 2: Shorts sobrecarregados (0-30 pts)
    if ls_ratio < SHORT_EXTREME_RATIO:
        score += 30.0
    elif ls_ratio < SHORT_HEAVY_RATIO:
        score += min(30.0, (1 - ls_ratio / SHORT_HEAVY_RATIO) * 30)

    # Fator 3: OI crescendo (0-20 pts)
    if oi_chg >= OI_GROWING_PCT:
        score += 20.0
    elif oi_chg >= 2.0:
        score += min(20.0, (oi_chg / OI_GROWING_PCT) * 12)

    # Fator 4: Shorts presos (bônus 0-15 pts)
    if trapped:
        score += 15.0

    # Bônus: preço subindo mesmo com shorts → squeeze acelerando
    if price_chg > 2.0 and ls_ratio < SHORT_HEAVY_RATIO:
        score = min(100.0, score * 1.20)

    return min(100.0, score)


def _empty_result(reason: str) -> dict:
    return {
        "short_squeeze_risk": False, "squeeze_probability": 0.0,
        "short_heavy": False,        "funding_negative": False,
        "oi_growing": False,         "funding_rate": 0.0,
        "long_short_ratio": 1.0,     "oi_change_pct": 0.0,
        "funding_annualized": 0.0,   "shorts_trapped": False,
        "details": {"error": reason},
    }
