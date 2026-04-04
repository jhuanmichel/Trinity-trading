"""
delta_engine.py — Delta Imbalance Detection
Trinity Trading | Institutional Direction Engine

Detecta:
- Buy delta vs Sell delta (via aproximação por candle)
- Absorção institucional: delta alto mas preço não se move
- Divergência: delta sobe, preço cai  → compra oculta
- Exaustão: delta extremo que começa a reverter

Método de delta por candle (CLV — Close Location Value):
  delta_ratio = (Close - Low) / (High - Low)   → [0, 1]
  buy_vol  = Volume × delta_ratio
  sell_vol = Volume × (1 - delta_ratio)
  delta    = buy_vol - sell_vol

  Lógica: quanto mais o fechamento está próximo da máxima, mais volume
  foi de compradores. Proxy robusto sem dados de tick.
"""

import logging
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ─── Configuração ─────────────────────────────────────────────────────────────

LOOKBACK_CANDLES      = 50     # candles para cálculo de delta
ABSORPTION_LOOKBACK   = 20     # janela de detecção de absorção
DIVERGENCE_LOOKBACK   = 20     # janela de divergência delta vs preço
ABSORPTION_THRESHOLD  = 0.58   # 58%+ buy ou sell = imbalance significativo
PRICE_FLAT_THRESHOLD  = 0.0012 # 0.12% = "preço não se moveu"


# ─── Cálculo de delta por candle ──────────────────────────────────────────────

def _candle_delta(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula o delta de cada candle via Close Location Value (CLV).

    delta_ratio → [0=todo venda, 1=toda compra]
    """
    df = df.copy()
    rng = df["High"].values - df["Low"].values

    # Evita divisão por zero em dojis
    delta_ratio = np.where(
        rng > 0,
        (df["Close"].values - df["Low"].values) / rng,
        0.5,
    )

    df["delta_ratio"] = delta_ratio
    df["buy_vol"]     = df["Volume"] * delta_ratio
    df["sell_vol"]    = df["Volume"] * (1 - delta_ratio)
    df["delta"]       = df["buy_vol"] - df["sell_vol"]
    df["cum_delta"]   = df["delta"].cumsum()
    return df


# ─── Absorção institucional ───────────────────────────────────────────────────

def _detect_absorption(df: pd.DataFrame, lookback: int = ABSORPTION_LOOKBACK) -> dict:
    """
    Absorção: fluxo de ordens pesado mas preço não reage proporcionalmente.

    BUY_ABSORPTION:  muito volume de compra + preço flat/baixando
                     → instituições VENDENDO para compradores retail

    SELL_ABSORPTION: muito volume de venda + preço flat/subindo
                     → instituições COMPRANDO da pressão vendedora retail
    """
    recent = df.iloc[-lookback:]

    total_buy  = float(recent["buy_vol"].sum())
    total_sell = float(recent["sell_vol"].sum())
    total_vol  = total_buy + total_sell

    if total_vol == 0:
        return {
            "absorption_detected":  False,
            "absorption_type":      None,
            "absorption_strength":  0.0,
            "buy_ratio":            0.5,
            "sell_ratio":           0.5,
            "price_change_pct":     0.0,
        }

    buy_ratio  = total_buy  / total_vol
    sell_ratio = total_sell / total_vol

    price_start  = float(recent["Close"].iloc[0])
    price_end    = float(recent["Close"].iloc[-1])
    price_change = (price_end - price_start) / price_start if price_start > 0 else 0.0

    absorption_detected = False
    absorption_type     = None
    absorption_strength = 0.0

    if buy_ratio >= ABSORPTION_THRESHOLD and price_change <= PRICE_FLAT_THRESHOLD:
        # Alto buy delta, preço não sobe → instituição vendendo (absorção de compra)
        absorption_detected = True
        absorption_type     = "BUY_ABSORPTION"
        # Força: quanto maior o imbalance e menor a resposta do preço, mais forte
        imbalance_excess = (buy_ratio - 0.50) * 2            # 0-1
        price_suppression = max(0.0, 1.0 - price_change * 500)  # 0-1
        absorption_strength = min(100.0, imbalance_excess * price_suppression * 100)

    elif sell_ratio >= ABSORPTION_THRESHOLD and price_change >= -PRICE_FLAT_THRESHOLD:
        # Alto sell delta, preço não cai → instituição comprando (absorção de venda)
        absorption_detected = True
        absorption_type     = "SELL_ABSORPTION"
        imbalance_excess    = (sell_ratio - 0.50) * 2
        price_suppression   = max(0.0, 1.0 + price_change * 500)
        absorption_strength = min(100.0, imbalance_excess * price_suppression * 100)

    return {
        "absorption_detected":  absorption_detected,
        "absorption_type":      absorption_type,
        "absorption_strength":  round(absorption_strength, 1),
        "buy_ratio":            round(buy_ratio,  3),
        "sell_ratio":           round(sell_ratio, 3),
        "price_change_pct":     round(price_change * 100, 3),
    }


# ─── Divergência delta vs preço ───────────────────────────────────────────────

def _detect_divergence(df: pd.DataFrame, lookback: int = DIVERGENCE_LOOKBACK) -> dict:
    """
    Divergência entre tendência do delta acumulado e tendência do preço.

    BULLISH_DIVERGENCE: preço cai, delta sobe → compra oculta (alta provável)
    BEARISH_DIVERGENCE: preço sobe, delta cai → venda oculta (queda provável)
    """
    if len(df) < lookback:
        return {"divergence": False, "divergence_type": None, "divergence_strength": 0.0}

    recent = df.iloc[-lookback:]
    x      = np.arange(len(recent))

    price_slope = np.polyfit(x, recent["Close"].values, 1)[0]
    delta_slope = np.polyfit(x, recent["cum_delta"].values, 1)[0]

    price_dir = int(np.sign(price_slope))
    delta_dir = int(np.sign(delta_slope))

    if price_dir == delta_dir or price_dir == 0 or delta_dir == 0:
        return {"divergence": False, "divergence_type": None, "divergence_strength": 0.0}

    # Divergência confirmada
    div_type = "BULLISH_DIVERGENCE" if (price_dir < 0 and delta_dir > 0) else "BEARISH_DIVERGENCE"

    # Força aproximada pela variação de preço no período
    price_change = abs(recent["Close"].iloc[-1] - recent["Close"].iloc[0]) / recent["Close"].iloc[0]
    div_strength = round(min(100.0, price_change * 1200), 1)

    return {
        "divergence":          True,
        "divergence_type":     div_type,
        "divergence_strength": div_strength,
        "price_slope_dir":     "UP" if price_slope > 0 else "DOWN",
        "delta_slope_dir":     "UP" if delta_slope > 0 else "DOWN",
    }


# ─── Exaustão de delta ────────────────────────────────────────────────────────

def _detect_exhaustion(df: pd.DataFrame, lookback: int = 10) -> dict:
    """
    Exaustão de delta: fluxo extremo que começa a se reverter.

    BUY_EXHAUSTION:  delta foi fortemente positivo, agora declina → virada baixista
    SELL_EXHAUSTION: delta foi fortemente negativo, agora sobe    → virada altista
    """
    if len(df) < lookback * 2:
        return {"exhaustion": False, "exhaustion_type": None}

    prior_delta  = float(df["delta"].iloc[-lookback * 2:-lookback].mean())
    recent_delta = float(df["delta"].iloc[-lookback:].mean())

    if abs(prior_delta) < 1e-8:
        return {"exhaustion": False, "exhaustion_type": None}

    change_ratio = (recent_delta - prior_delta) / abs(prior_delta)

    # Exaustão de compra: era positivo, caiu ≥ 40%
    if prior_delta > 0 and change_ratio < -0.40:
        return {"exhaustion": True, "exhaustion_type": "BUY_EXHAUSTION"}

    # Exaustão de venda: era negativo, subiu ≥ 40%
    if prior_delta < 0 and change_ratio > 0.40:
        return {"exhaustion": True, "exhaustion_type": "SELL_EXHAUSTION"}

    return {"exhaustion": False, "exhaustion_type": None}


# ─── Run ─────────────────────────────────────────────────────────────────────

def run_delta_engine(ohlcv_15m: list, ohlcv_1h: list) -> dict:
    """
    Engine de Delta Imbalance.

    Args:
        ohlcv_15m: [[ts, open, high, low, close, volume], ...] candles 15m
        ohlcv_1h:  [[ts, open, high, low, close, volume], ...] candles 1H

    Returns:
        {
            "delta_imbalance":     bool,
            "delta_type":          "BUY_HEAVY" | "SELL_HEAVY" | "BALANCED",
            "absorption_detected": bool,
            "absorption_type":     str | None,
            "absorption_strength": float 0-100,
            "divergence_detected": bool,
            "divergence_type":     str | None,
            "divergence_strength": float 0-100,
            "exhaustion_detected": bool,
            "exhaustion_type":     str | None,
            "institutional_bias":  "BULLISH" | "BEARISH" | "NEUTRAL",
            "confidence":          float 0-100,
            "delta_score":         float 0-100,
            "net_delta_ratio":     float -1 to +1,
            "buy_ratio":           float 0-1,
        }
    """
    _neutral = {
        "delta_imbalance":     False,
        "delta_type":          "BALANCED",
        "absorption_detected": False,
        "absorption_type":     None,
        "absorption_strength": 0.0,
        "divergence_detected": False,
        "divergence_type":     None,
        "divergence_strength": 0.0,
        "exhaustion_detected": False,
        "exhaustion_type":     None,
        "institutional_bias":  "NEUTRAL",
        "confidence":          50.0,
        "delta_score":         50.0,
        "net_delta_ratio":     0.0,
        "buy_ratio":           0.5,
    }

    try:
        cols = ["Open", "High", "Low", "Close", "Volume"]

        # 15m: fluxo de curto prazo (principal para absorção)
        df_15m = (
            pd.DataFrame(ohlcv_15m, columns=["ts"] + cols)
            .astype(float)
            .drop("ts", axis=1)
            .tail(LOOKBACK_CANDLES)
            .reset_index(drop=True)
        )

        # 1H: divergência de médio prazo
        df_1h = (
            pd.DataFrame(ohlcv_1h, columns=["ts"] + cols)
            .astype(float)
            .drop("ts", axis=1)
            .tail(LOOKBACK_CANDLES)
            .reset_index(drop=True)
        )

        if len(df_15m) < 10 or len(df_1h) < 10:
            return _neutral

        # Calcula delta em ambos os timeframes
        df_15m = _candle_delta(df_15m)
        df_1h  = _candle_delta(df_1h)

        # ── Net Delta Ratio (15m, janela completa) ────────────────────────
        total_buy_15m  = float(df_15m["buy_vol"].sum())
        total_sell_15m = float(df_15m["sell_vol"].sum())
        total_15m      = total_buy_15m + total_sell_15m

        if total_15m > 0:
            buy_ratio_15m   = total_buy_15m / total_15m
            net_delta_ratio = (buy_ratio_15m - 0.5) * 2   # -1 a +1

            if   buy_ratio_15m >= ABSORPTION_THRESHOLD:
                delta_type      = "BUY_HEAVY"
                delta_imbalance = True
            elif buy_ratio_15m <= (1 - ABSORPTION_THRESHOLD):
                delta_type      = "SELL_HEAVY"
                delta_imbalance = True
            else:
                delta_type      = "BALANCED"
                delta_imbalance = False
        else:
            buy_ratio_15m   = 0.5
            net_delta_ratio = 0.0
            delta_type      = "BALANCED"
            delta_imbalance = False

        # ── Absorção (15m, janela curta) ──────────────────────────────────
        absorption = _detect_absorption(df_15m, lookback=ABSORPTION_LOOKBACK)

        # ── Divergência (1H) ──────────────────────────────────────────────
        divergence = _detect_divergence(df_1h, lookback=DIVERGENCE_LOOKBACK)

        # ── Exaustão (15m) ────────────────────────────────────────────────
        exhaustion = _detect_exhaustion(df_15m, lookback=10)

        # ── Bias institucional ────────────────────────────────────────────
        # Absorção é o sinal mais confiável (inversão do fluxo de retail)
        bull_pts = 0
        bear_pts = 0

        if absorption["absorption_type"] == "SELL_ABSORPTION":
            bull_pts += 3   # instituição comprando = bullish
        elif absorption["absorption_type"] == "BUY_ABSORPTION":
            bear_pts += 3   # instituição vendendo  = bearish

        if divergence["divergence_type"] == "BULLISH_DIVERGENCE":
            bull_pts += 2
        elif divergence["divergence_type"] == "BEARISH_DIVERGENCE":
            bear_pts += 2

        if exhaustion["exhaustion_type"] == "SELL_EXHAUSTION":
            bull_pts += 1   # venda se exauriu = reversão alta
        elif exhaustion["exhaustion_type"] == "BUY_EXHAUSTION":
            bear_pts += 1   # compra se exauriu = reversão baixa

        # Delta bruto (menos peso: instituições se escondem no delta oposto)
        if delta_type == "BUY_HEAVY" and not absorption["absorption_detected"]:
            bull_pts += 1
        elif delta_type == "SELL_HEAVY" and not absorption["absorption_detected"]:
            bear_pts += 1

        total_pts = bull_pts + bear_pts
        if total_pts == 0:
            institutional_bias = "NEUTRAL"
            confidence         = 50.0
        elif bull_pts > bear_pts:
            institutional_bias = "BULLISH"
            confidence         = 50.0 + (bull_pts / total_pts - 0.5) * 100
        else:
            institutional_bias = "BEARISH"
            confidence         = 50.0 + (bear_pts / total_pts - 0.5) * 100

        confidence = round(min(92.0, max(15.0, confidence)), 1)

        # ── Delta Score (0-100 para o modelo direcional) ─────────────────
        if institutional_bias == "BULLISH":
            delta_score = 50.0 + (confidence - 50.0)
        elif institutional_bias == "BEARISH":
            delta_score = 50.0 - (confidence - 50.0)
        else:
            delta_score = 50.0

        # Boost por força da absorção
        if absorption["absorption_detected"] and absorption["absorption_strength"] > 20:
            boost = absorption["absorption_strength"] * 0.12
            if institutional_bias == "BULLISH":
                delta_score = min(100.0, delta_score + boost)
            elif institutional_bias == "BEARISH":
                delta_score = max(0.0, delta_score - boost)

        return {
            "delta_imbalance":     delta_imbalance,
            "delta_type":          delta_type,
            "absorption_detected": absorption["absorption_detected"],
            "absorption_type":     absorption["absorption_type"],
            "absorption_strength": absorption["absorption_strength"],
            "divergence_detected": divergence["divergence"],
            "divergence_type":     divergence.get("divergence_type"),
            "divergence_strength": divergence.get("divergence_strength", 0.0),
            "exhaustion_detected": exhaustion["exhaustion"],
            "exhaustion_type":     exhaustion["exhaustion_type"],
            "institutional_bias":  institutional_bias,
            "confidence":          confidence,
            "delta_score":         round(min(100.0, max(0.0, delta_score)), 1),
            "net_delta_ratio":     round(net_delta_ratio, 3),
            "buy_ratio":           round(buy_ratio_15m, 3),
        }

    except Exception as e:
        log.warning(f"   Delta Engine erro: {e}", exc_info=False)
        return _neutral
