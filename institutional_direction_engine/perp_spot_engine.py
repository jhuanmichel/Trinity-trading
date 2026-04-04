"""
perp_spot_engine.py — Perpetual vs Spot Divergence Engine
Trinity Trading | Institutional Direction Engine

Detecta:
- Perp volume dominando vs Spot volume dominando
- Moves falsos (derivativo) vs moves reais (spot)
- Funding rate extremo → crowded trade → risco de squeeze/cascata
- Premium do perp → bias direcional do mercado alavancado
- OI rising com preço = momentum real; OI falling = desalavancagem

Interpretação central:
  Spot dominante + volume expandindo  → move REAL e sustentável
  Perp dominante + funding extremo    → move FALSO / manipulação
  Funding negativo extremo            → shorts armadilhados → squeeze UP
  Funding positivo extremo            → longs armadilhados → cascade DOWN
"""

import logging
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ─── Configuração ─────────────────────────────────────────────────────────────

VOLUME_DOM_THRESHOLD   = 0.62    # 62% = mercado dominante declarado
FUNDING_EXTREME_POS    = +0.0003 # +0.03% = longs excessivos (bearish)
FUNDING_EXTREME_NEG    = -0.0003 # -0.03% = shorts excessivos (bullish squeeze)
PERP_PREMIUM_BULL      = +0.0020 # +0.20% = perp premium = bullish futures bias
PERP_PREMIUM_BEAR      = -0.0020 # -0.20% = perp discount = bearish futures bias
VOLUME_EXPANSION_RATIO = 1.30    # 30% de expansão de volume = significativo


# ─── Volume & Momentum Metrics ────────────────────────────────────────────────

def _volume_momentum(df: pd.DataFrame, lookback: int = 20) -> dict:
    """Calcula volume médio, tendência de volume e momentum de preço."""
    recent = df.tail(lookback)
    if len(recent) < 5:
        return {"avg_vol": 0.0, "recent_vol": 0.0, "vol_trend": "STABLE", "momentum": 0.0}

    avg_vol    = float(recent["Volume"].mean())
    recent_vol = float(recent["Volume"].tail(5).mean())
    prior_vol  = float(recent["Volume"].head(5).mean())

    if prior_vol > 0:
        ratio = recent_vol / prior_vol
        if   ratio >= VOLUME_EXPANSION_RATIO: vol_trend = "EXPANDING"
        elif ratio <= (1 / VOLUME_EXPANSION_RATIO): vol_trend = "CONTRACTING"
        else: vol_trend = "STABLE"
    else:
        vol_trend = "STABLE"

    p_start  = float(recent["Close"].iloc[0])
    p_end    = float(recent["Close"].iloc[-1])
    momentum = (p_end - p_start) / p_start if p_start > 0 else 0.0

    return {
        "avg_vol":    avg_vol,
        "recent_vol": recent_vol,
        "vol_trend":  vol_trend,
        "momentum":   momentum,
    }


# ─── Move Validity ────────────────────────────────────────────────────────────

def _assess_move_validity(spot: dict, perp: dict,
                          funding_rate: float) -> dict:
    """
    Determina se um move de preço é REAL, FRACO ou FALSO.

    REAL:  Spot domina + expanding volume → move genuíno de investidores
    FAKE:  Perp domina + spot fraco + funding extremo → alavancagem especulativa
    WEAK:  Ambos moderados, sem divergência clara
    """
    sv = spot.get("recent_vol", 0)
    pv = perp.get("recent_vol", 0)
    total = sv + pv

    if total == 0:
        return {"move_validity": "WEAK", "dominant_market": "BALANCED",
                "spot_ratio": 0.5, "perp_ratio": 0.5}

    spot_ratio = sv / total
    perp_ratio = pv / total

    if   spot_ratio >= VOLUME_DOM_THRESHOLD: dominant = "SPOT"
    elif perp_ratio >= VOLUME_DOM_THRESHOLD: dominant = "PERP"
    else:                                    dominant = "BALANCED"

    spot_expanding = spot.get("vol_trend") == "EXPANDING"
    perp_expanding = perp.get("vol_trend") == "EXPANDING"

    # Classificação base
    if dominant == "SPOT" and spot_expanding:
        move_validity = "REAL"
    elif dominant == "PERP" and not spot_expanding:
        move_validity = "FAKE"
    else:
        move_validity = "WEAK"

    # Funding extremo degrada validade
    if abs(funding_rate) >= FUNDING_EXTREME_POS:
        if move_validity == "REAL":
            move_validity = "WEAK"   # mesmo move real perde força com alavancagem excessiva
        elif move_validity == "WEAK":
            move_validity = "FAKE"

    return {
        "move_validity":   move_validity,
        "dominant_market": dominant,
        "spot_ratio":      round(spot_ratio, 3),
        "perp_ratio":      round(perp_ratio, 3),
        "spot_expanding":  spot_expanding,
        "perp_expanding":  perp_expanding,
    }


# ─── Funding Analysis ────────────────────────────────────────────────────────

def _analyze_funding(funding_rate: float) -> dict:
    """
    Analisa funding rate e extrai bias direcional.

    Funding positivo alto  → longs pagando = muito long → risco de dump
    Funding negativo alto  → shorts pagando = muito short → risco de squeeze
    """
    fr = funding_rate or 0.0

    if   fr >= FUNDING_EXTREME_POS:
        bias    = "BEARISH"          # longs excessivos
        extreme = True
    elif fr <= FUNDING_EXTREME_NEG:
        bias    = "BULLISH"          # shorts excessivos → squeeze
        extreme = True
    elif fr > 0.0001:
        bias    = "SLIGHTLY_BEARISH"
        extreme = False
    elif fr < -0.0001:
        bias    = "SLIGHTLY_BULLISH"
        extreme = False
    else:
        bias    = "NEUTRAL"
        extreme = False

    # Taxa anualizada (3 settlements/dia × 365)
    annual_pct = round(fr * 3 * 365 * 100, 2)

    return {
        "funding_bias":    bias,
        "funding_extreme": extreme,
        "funding_rate":    fr,
        "annual_pct":      annual_pct,
    }


# ─── Directional Bias ────────────────────────────────────────────────────────

def _derive_bias(spot: dict, perp: dict, validity: dict,
                 funding: dict, oi_change_pct: float,
                 perp_premium: float) -> dict:
    """
    Deriva bias direcional combinando todos os sinais perp/spot.

    Pontuação:
      Spot acumulando (vol UP + preço UP) → +3 bull
      Spot distribuindo (vol UP + preço DOWN) → +3 bear
      Funding squeeze setup (negativo extremo) → +2 bull
      Funding longs armadilhados (positivo extremo) → +2 bear
      OI subindo + preço subindo → +2 bull
      OI caindo + preço subindo → +1 bear (desalavancagem no topo)
      Perp premium positivo → +1 bull
      Fake pump (perp domina, spot fraco) → +2 bear
      Fake dump (perp domina, spot fraco, preço cai) → +2 bull
    """
    bull = 0
    bear = 0
    signals = []

    spot_expanding = spot.get("vol_trend") == "EXPANDING"
    spot_momentum  = spot.get("momentum", 0.0)
    dominant       = validity.get("dominant_market", "BALANCED")
    move_valid     = validity.get("move_validity", "WEAK")

    # Spot signals
    if spot_expanding and spot_momentum > 0.001:
        bull += 3; signals.append("spot_accumulation")
    elif spot_expanding and spot_momentum < -0.001:
        bear += 3; signals.append("spot_distribution")

    # Funding signals
    f_bias = funding.get("funding_bias", "NEUTRAL")
    if f_bias == "BULLISH":
        bull += 2; signals.append("funding_squeeze_setup")
    elif f_bias == "SLIGHTLY_BULLISH":
        bull += 1; signals.append("funding_slightly_bullish")
    elif f_bias == "BEARISH":
        bear += 2; signals.append("funding_longs_trapped")
    elif f_bias == "SLIGHTLY_BEARISH":
        bear += 1; signals.append("funding_slightly_bearish")

    # OI signals
    if oi_change_pct > 5.0:
        if spot_momentum > 0:
            bull += 2; signals.append("oi_rising_price_up")
        else:
            bear += 1; signals.append("oi_rising_price_down")
    elif oi_change_pct < -5.0:
        signals.append("oi_declining_deleveraging")

    # Perp premium
    if perp_premium >= PERP_PREMIUM_BULL:
        bull += 1; signals.append("perp_premium_bullish")
    elif perp_premium <= PERP_PREMIUM_BEAR:
        bear += 1; signals.append("perp_discount_bearish")

    # Move validity reversals
    if move_valid == "FAKE":
        perp_mom = perp.get("momentum", 0.0)
        if perp_mom > 0:
            bear += 2; signals.append("fake_pump_reversion_risk")
        else:
            bull += 2; signals.append("fake_dump_reversion_risk")
    elif move_valid == "REAL":
        # Real move sustentado na direção do spot
        if spot_momentum > 0:
            bull += 1
        elif spot_momentum < 0:
            bear += 1

    total = bull + bear
    if total == 0:
        return {
            "directional_bias": "NEUTRAL",
            "confidence":       50.0,
            "bull_pts":         0,
            "bear_pts":         0,
            "signals":          signals,
        }

    if bull > bear:
        bias       = "UP"
        confidence = 50.0 + (bull / total - 0.5) * 100
    else:
        bias       = "DOWN"
        confidence = 50.0 + (bear / total - 0.5) * 100

    return {
        "directional_bias": bias,
        "confidence":       round(min(92.0, max(15.0, confidence)), 1),
        "bull_pts":         bull,
        "bear_pts":         bear,
        "signals":          signals,
    }


# ─── Run ─────────────────────────────────────────────────────────────────────

def run_perp_spot_engine(
    ohlcv_spot_1h: list,
    ohlcv_perp_1h: list,
    funding_rate:  float = 0.0,
    oi_change_pct: float = 0.0,
    perp_premium:  float = 0.0,
) -> dict:
    """
    Engine de Divergência Perp vs Spot.

    Args:
        ohlcv_spot_1h: [[ts, open, high, low, close, volume], ...] — mercado spot
        ohlcv_perp_1h: [[ts, open, high, low, close, volume], ...] — mercado perp
        funding_rate:  funding rate atual (ex: 0.0001 = 0.01%)
        oi_change_pct: variação do OI em % nas últimas 24h
        perp_premium:  (perp_price - spot_price) / spot_price

    Returns:
        {
            "perp_vs_spot_divergence": bool,
            "dominant_market":         "SPOT" | "PERP" | "BALANCED",
            "move_validity":           "REAL" | "WEAK" | "FAKE",
            "directional_bias":        "UP" | "DOWN" | "NEUTRAL",
            "fake_move_probability":   float 0-100,
            "confidence":              float 0-100,
            "perp_spot_score":         float 0-100,
            "funding_bias":            str,
            "funding_extreme":         bool,
            "funding_rate":            float,
            "funding_annual_pct":      float,
            "spot_vol_trend":          str,
            "perp_vol_trend":          str,
            "oi_change_pct":           float,
            "perp_premium":            float,
            "signals":                 list,
        }
    """
    _neutral = {
        "perp_vs_spot_divergence": False,
        "dominant_market":         "BALANCED",
        "move_validity":           "WEAK",
        "directional_bias":        "NEUTRAL",
        "fake_move_probability":   30.0,
        "confidence":              50.0,
        "perp_spot_score":         50.0,
        "funding_bias":            "NEUTRAL",
        "funding_extreme":         False,
        "funding_rate":            0.0,
        "funding_annual_pct":      0.0,
        "spot_vol_trend":          "STABLE",
        "perp_vol_trend":          "STABLE",
        "oi_change_pct":           0.0,
        "perp_premium":            0.0,
        "signals":                 [],
    }

    try:
        cols = ["Open", "High", "Low", "Close", "Volume"]

        df_spot = (
            pd.DataFrame(ohlcv_spot_1h, columns=["ts"] + cols)
            .astype(float)
            .drop("ts", axis=1)
        )

        # Perp pode não existir — usa spot como proxy com volume zero
        if ohlcv_perp_1h and len(ohlcv_perp_1h) >= 10:
            df_perp = (
                pd.DataFrame(ohlcv_perp_1h, columns=["ts"] + cols)
                .astype(float)
                .drop("ts", axis=1)
            )
        else:
            df_perp = df_spot.copy()
            df_perp["Volume"] = 0.0  # sem dados perp → spot domina

        if len(df_spot) < 10:
            return _neutral

        # Métricas
        spot_metrics = _volume_momentum(df_spot, lookback=20)
        perp_metrics = _volume_momentum(df_perp, lookback=20)

        # Validade do move
        validity = _assess_move_validity(spot_metrics, perp_metrics, funding_rate)

        # Análise de funding
        funding = _analyze_funding(funding_rate)

        # Bias direcional
        bias_result = _derive_bias(
            spot_metrics, perp_metrics,
            validity, funding,
            oi_change_pct, perp_premium,
        )

        # Fake move probability
        fake_base = {"REAL": 15.0, "WEAK": 45.0, "FAKE": 72.0}
        fake_prob = fake_base.get(validity["move_validity"], 40.0)

        if funding["funding_extreme"]:
            fake_prob = min(90.0, fake_prob + 12.0)  # funding extremo → mais chance de fake

        # Divergência declarada quando há mercado dominante
        divergence_detected = validity["dominant_market"] != "BALANCED"

        # perp_spot_score: 0-100 → >50 bullish, <50 bearish
        dir_bias   = bias_result["directional_bias"]
        confidence = bias_result["confidence"]

        if   dir_bias == "UP":
            perp_spot_score = 50.0 + (confidence - 50.0)
        elif dir_bias == "DOWN":
            perp_spot_score = 50.0 - (confidence - 50.0)
        else:
            perp_spot_score = 50.0

        return {
            "perp_vs_spot_divergence": divergence_detected,
            "dominant_market":         validity["dominant_market"],
            "move_validity":           validity["move_validity"],
            "directional_bias":        dir_bias,
            "fake_move_probability":   round(fake_prob, 1),
            "confidence":              confidence,
            "perp_spot_score":         round(min(100.0, max(0.0, perp_spot_score)), 1),
            "funding_bias":            funding["funding_bias"],
            "funding_extreme":         funding["funding_extreme"],
            "funding_rate":            funding_rate,
            "funding_annual_pct":      funding["annual_pct"],
            "spot_vol_trend":          spot_metrics["vol_trend"],
            "perp_vol_trend":          perp_metrics["vol_trend"],
            "oi_change_pct":           oi_change_pct,
            "perp_premium":            perp_premium,
            "signals":                 bias_result["signals"],
        }

    except Exception as e:
        log.warning(f"   Perp/Spot Engine erro: {e}", exc_info=False)
        return _neutral
