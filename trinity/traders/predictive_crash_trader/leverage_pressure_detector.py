"""
leverage_pressure_detector.py — Detector de Pressão de Alavancagem

Detecta setups de cascata por excesso de alavancagem:
  - Spike de Open Interest com preço estagnando (divergência OI/preço)
  - Funding rate extremo (longs pagando muito → mercado sobrecarregado)
  - Long/Short ratio desequilibrado (muitos longs = alvo fácil)
  - OI crescendo sem breakout de preço = armadilha de longs

Lógica:
  oi_div_score      = OI subindo mas preço caindo → divergência = risco
  funding_score     = funding > EXTREME_FUNDING → penalidade
  ls_ratio_score    = long_ratio > LS_LONG_HEAVY → longs sobrecarregados

cascade_probability [0-100]:
  - 100 → OI spike + funding alto + longs sobrepesados → cascata iminente
  -   0 → alavancagem saudável e equilibrada

Saída:
  {
    "cascade_probability": 0-100,
    "oi_spike":            bool,
    "funding_extreme":     bool,
    "long_heavy":          bool,
    "funding_rate":        float,   # taxa atual (ex: 0.001 = 0.1%)
    "oi_change_pct":       float,   # variação do OI (%)
    "long_short_ratio":    float,   # ratio long/short
    "oi_price_divergence": bool,    # OI subindo + preço caindo
    "funding_annualized":  float,   # taxa anualizada (%)
    "details":             dict,
  }
"""
import logging

log = logging.getLogger(__name__)

# ── Limiares ──────────────────────────────────────────────────────────────────
FUNDING_EXTREME_RATE    = 0.0008  # funding > 0.08% por 8h = extremo
FUNDING_HIGH_RATE       = 0.0004  # funding > 0.04% = alto
OI_SPIKE_PCT            = 8.0     # OI cresceu > 8% em ~1h = spike
OI_MODERATE_PCT         = 4.0     # OI cresceu > 4% = moderado
LS_LONG_HEAVY           = 1.7     # long/short > 1.7 = longs sobrecarregados
LS_EXTREME_LONG         = 2.5     # long/short > 2.5 = extremo
PRICE_STAGNANT_PCT      = 0.5     # preço variou menos de 0.5% = estagnado
FUNDING_8H_PER_YEAR     = 3 * 365 # 3 sessões de 8h por dia * 365 dias


def detect_leverage_pressure(coin_data: dict) -> dict:
    """
    Detecta pressão de alavancagem excessiva.

    Args:
        coin_data: dict com "price", "price_change_pct", "funding_rate",
                   "oi_change_pct", "long_short_ratio", "open_interest"

    Returns:
        dict com cascade_probability e detalhes
    """
    price              = float(coin_data.get("price", 0))
    price_change_pct   = float(coin_data.get("price_change_pct", 0))
    funding_rate       = float(coin_data.get("funding_rate", 0))
    oi_change_pct      = float(coin_data.get("oi_change_pct", 0))
    long_short_ratio   = float(coin_data.get("long_short_ratio", 1.0))
    open_interest      = float(coin_data.get("open_interest", 0))

    if not price:
        return _empty_result("Sem dados de preço")

    # ── Indicadores binários ───────────────────────────────────────────────
    oi_spike     = oi_change_pct >= OI_SPIKE_PCT
    funding_extreme = funding_rate >= FUNDING_EXTREME_RATE
    long_heavy   = long_short_ratio >= LS_LONG_HEAVY

    # Divergência OI/preço: OI subindo mas preço caindo ou estagnando
    oi_price_divergence = (
        oi_change_pct >= OI_MODERATE_PCT
        and price_change_pct <= PRICE_STAGNANT_PCT
    )

    # Funding anualizado
    funding_annualized = funding_rate * FUNDING_8H_PER_YEAR * 100

    # ── cascade_probability [0-100] ───────────────────────────────────────
    cascade_prob = _calc_cascade_probability(
        funding_rate, oi_change_pct, long_short_ratio,
        price_change_pct, oi_price_divergence,
    )

    log.debug(
        f"[LevPress] cascade={cascade_prob:.0f} | funding={funding_rate:.4f} "
        f"oi_chg={oi_change_pct:.1f}% L/S={long_short_ratio:.2f} "
        f"div={oi_price_divergence}"
    )

    return {
        "cascade_probability": round(cascade_prob, 1),
        "oi_spike":            oi_spike,
        "funding_extreme":     funding_extreme,
        "long_heavy":          long_heavy,
        "funding_rate":        funding_rate,
        "oi_change_pct":       round(oi_change_pct, 2),
        "long_short_ratio":    round(long_short_ratio, 2),
        "oi_price_divergence": oi_price_divergence,
        "funding_annualized":  round(funding_annualized, 1),
        "details": {
            "open_interest_usd": round(open_interest),
            "price_change_pct":  round(price_change_pct, 2),
            "funding_per_8h":    f"{funding_rate * 100:.4f}%",
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc_cascade_probability(
    funding: float,
    oi_chg: float,
    ls_ratio: float,
    price_chg: float,
    oi_div: bool,
) -> float:
    """Calcula cascade_probability [0-100]."""
    score = 0.0

    # Fator 1: Funding rate (0-35 pts)
    if funding >= FUNDING_EXTREME_RATE:
        score += 35.0
    elif funding >= FUNDING_HIGH_RATE:
        score += min(35.0, (funding / FUNDING_EXTREME_RATE) * 25)

    # Fator 2: OI spike (0-25 pts)
    if oi_chg >= OI_SPIKE_PCT:
        score += 25.0
    elif oi_chg >= OI_MODERATE_PCT:
        score += min(25.0, (oi_chg / OI_SPIKE_PCT) * 15)

    # Fator 3: Long heavy (0-25 pts)
    if ls_ratio >= LS_EXTREME_LONG:
        score += 25.0
    elif ls_ratio >= LS_LONG_HEAVY:
        score += min(25.0, ((ls_ratio - 1.0) / (LS_EXTREME_LONG - 1.0)) * 25)

    # Fator 4: Divergência OI/preço (0-15 pts bônus)
    if oi_div:
        score += 15.0

    # Bônus: preço caindo com alavancagem alta → urgência adicional
    if price_chg < -2.0 and ls_ratio >= LS_LONG_HEAVY:
        score = min(100.0, score * 1.20)

    return min(100.0, score)


def _empty_result(reason: str) -> dict:
    return {
        "cascade_probability": 0.0,
        "oi_spike":            False,
        "funding_extreme":     False,
        "long_heavy":          False,
        "funding_rate":        0.0,
        "oi_change_pct":       0.0,
        "long_short_ratio":    1.0,
        "oi_price_divergence": False,
        "funding_annualized":  0.0,
        "details":             {"error": reason},
    }
