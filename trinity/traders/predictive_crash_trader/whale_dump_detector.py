"""
whale_dump_detector.py — Detector de Dump de Baleias

Detecta sinais de venda institucional / whale:
  - Concentração de grandes ordens de venda no orderbook
  - CVD (Cumulative Volume Delta) negativo extremo
  - Ratio de pressão vendedora vs compradora em trades recentes
  - Desequilíbrio de volume nas últimas velas

Lógica:
  large_order_threshold = max(10 BTC | 1000 SOL | etc.)
  sell_pressure_ratio   = sell_volume / (buy_volume + sell_volume) nos últimos N trades
  cvd_score             = CVD normalizado pelo volume total

Risk [0-100]:
  - 100 → dump massivo iminente (whales saindo, CVD extremamente negativo)
  -   0 → sem pressão de venda institucional detectada

Saída:
  {
    "risk":                 0-100,
    "large_sell_detected":  bool,
    "cvd_negative":         bool,
    "sell_pressure_ratio":  float,   # [0, 1] — 0.5 = neutro, >0.7 = pressão
    "estimated_dump_usd":   float,   # volume de venda estimado em USD
    "whale_sell_count":     int,     # número de grandes ordens de venda
    "cvd_score":            float,   # CVD normalizado [-1, +1]
    "details":              dict,
  }
"""
import logging
from typing import List

log = logging.getLogger(__name__)

# ── Limiares ──────────────────────────────────────────────────────────────────
SELL_PRESSURE_HIGH      = 0.65   # acima de 65% de pressão vendedora → alto
SELL_PRESSURE_EXTREME   = 0.78   # acima de 78% → dump whale
CVD_NEGATIVE_THRESHOLD  = -0.30  # CVD normalizado abaixo de -0.30 → negativo
LARGE_TRADE_MULT        = 5.0    # trade > 5× avg_trade_size = "grande"
MIN_LARGE_TRADE_USD     = 50_000 # mínimo $50K para considerar "whale"


def detect_whale_dump(coin_data: dict) -> dict:
    """
    Detecta pressão de venda de baleias.

    Args:
        coin_data: dict com "price", "recent_trades" (aggTrades Binance)
                   e "klines_15m" para CVD de velas

    Returns:
        dict com risk e detalhes
    """
    price          = float(coin_data.get("price", 0))
    recent_trades  = coin_data.get("recent_trades", [])
    klines         = coin_data.get("klines_15m", [])

    if not price:
        return _empty_result("Sem preço")

    # ── Análise de trades recentes ─────────────────────────────────────────
    buy_vol  = 0.0
    sell_vol = 0.0
    whale_sells    = 0
    total_dump_usd = 0.0

    if recent_trades:
        trade_sizes = []
        for t in recent_trades:
            try:
                qty    = float(t.get("q", 0))
                tprice = float(t.get("p", price))
                is_sell = bool(t.get("m", False))  # m=True → maker=buy → taker=sell
                usd_val = qty * tprice
                trade_sizes.append(usd_val)
                if is_sell:
                    sell_vol += usd_val
                else:
                    buy_vol += usd_val
            except (ValueError, TypeError):
                continue

        # Detecta trades grandes (whale)
        avg_size = sum(trade_sizes) / len(trade_sizes) if trade_sizes else 0
        for t in recent_trades:
            try:
                qty    = float(t.get("q", 0))
                tprice = float(t.get("p", price))
                is_sell = bool(t.get("m", False))
                usd_val = qty * tprice
                if is_sell and usd_val >= max(MIN_LARGE_TRADE_USD, avg_size * LARGE_TRADE_MULT):
                    whale_sells    += 1
                    total_dump_usd += usd_val
            except (ValueError, TypeError):
                continue

    total_vol           = buy_vol + sell_vol
    sell_pressure_ratio = sell_vol / total_vol if total_vol > 0 else 0.5

    # ── CVD de velas ──────────────────────────────────────────────────────
    cvd_score = _calc_cvd_from_klines(klines)

    # ── Indicadores ───────────────────────────────────────────────────────
    large_sell_detected = whale_sells >= 2 or total_dump_usd >= MIN_LARGE_TRADE_USD * 5
    cvd_negative        = cvd_score < CVD_NEGATIVE_THRESHOLD

    # ── Risk Score ────────────────────────────────────────────────────────
    risk = _calc_risk(
        sell_pressure_ratio, cvd_score,
        whale_sells, total_dump_usd,
    )

    log.debug(
        f"[WhaDump] risk={risk:.0f} | press={sell_pressure_ratio:.2f} "
        f"cvd={cvd_score:.2f} | whale_sells={whale_sells} "
        f"dump=${total_dump_usd/1e3:.0f}K"
    )

    return {
        "risk":                round(risk, 1),
        "large_sell_detected": large_sell_detected,
        "cvd_negative":        cvd_negative,
        "sell_pressure_ratio": round(sell_pressure_ratio, 3),
        "estimated_dump_usd":  round(total_dump_usd),
        "whale_sell_count":    whale_sells,
        "cvd_score":           round(cvd_score, 3),
        "details": {
            "buy_vol_usd":    round(buy_vol),
            "sell_vol_usd":   round(sell_vol),
            "total_trades":   len(recent_trades),
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc_cvd_from_klines(klines: list) -> float:
    """
    Calcula CVD normalizado [-1, +1] a partir de klines OHLCV.

    CVD simplificado: se close > open → bullish vol (+), se close < open → bearish vol (-)
    """
    if not klines:
        return 0.0

    cumulative = 0.0
    total_vol  = 0.0

    for k in klines[-20:]:  # últimas 20 velas 15m = ~5 horas
        try:
            open_p  = float(k[1])
            close_p = float(k[4])
            vol     = float(k[5])
            # Volume ponderado pela direção
            if close_p > open_p:
                cumulative += vol
            else:
                cumulative -= vol
            total_vol += vol
        except (ValueError, TypeError, IndexError):
            continue

    if total_vol == 0:
        return 0.0

    return max(-1.0, min(1.0, cumulative / total_vol))


def _calc_risk(
    sell_ratio: float,
    cvd: float,
    whale_count: int,
    dump_usd: float,
) -> float:
    """Calcula risk [0-100]."""
    score = 0.0

    # Fator 1: Pressão vendedora (0-40 pts)
    if sell_ratio > 0.5:
        score += min(40.0, (sell_ratio - 0.5) / 0.5 * 40)

    # Fator 2: CVD negativo (0-30 pts)
    if cvd < 0:
        score += min(30.0, abs(cvd) * 30)

    # Fator 3: Whale sells (0-20 pts)
    score += min(20.0, whale_count * 5.0)

    # Fator 4: Volume de dump (0-10 pts)
    if dump_usd > MIN_LARGE_TRADE_USD:
        score += min(10.0, (dump_usd / (MIN_LARGE_TRADE_USD * 20)) * 10)

    return min(100.0, score)


def _empty_result(reason: str) -> dict:
    return {
        "risk":                0.0,
        "large_sell_detected": False,
        "cvd_negative":        False,
        "sell_pressure_ratio": 0.5,
        "estimated_dump_usd":  0.0,
        "whale_sell_count":    0,
        "cvd_score":           0.0,
        "details":             {"error": reason},
    }
