"""
whale_accumulation_detector.py — Detector de Acumulação de Baleias

Detecta compra institucional oculta ANTES do pump:
  - Grandes ordens de compra absorvendo o orderbook (bids crescendo)
  - CVD positivo crescente (volume delta cumulativo comprador)
  - Ratio de pressão compradora vs vendedora elevado
  - Padrão de absorção: asks sendo comidas sem queda de preço

Lógica:
  buy_pressure_ratio = buy_volume / total_volume nos últimos N trades
  large_buy_orders   = ordens > LARGE_ORDER_MULT × avg_trade_size
  cvd_score          = CVD normalizado [−1, +1]
  absorption         = preço não cai mesmo com sell pressure (suporte)

accumulation_strength [0-100]:
  - 100 → acumulação institucional massiva detectada (pump iminente)
  -   0 → sem sinais de acumulação

Saída:
  {
    "whale_accumulation":    bool,
    "accumulation_strength": 0-100,
    "large_buy_detected":    bool,
    "cvd_positive":          bool,
    "buy_pressure_ratio":    float,    # [0,1] — >0.60 = pressão compradora
    "estimated_buy_usd":     float,    # volume de compra estimado em USD
    "whale_buy_count":       int,
    "cvd_score":             float,    # [−1, +1]
    "details":               dict,
  }
"""
import logging

log = logging.getLogger(__name__)

BUY_PRESSURE_HIGH    = 0.60   # acima de 60% compradora → pressão
BUY_PRESSURE_STRONG  = 0.72   # acima de 72% → acumulação forte
CVD_POSITIVE_THRESH  = 0.25   # CVD > +0.25 = acumulação
LARGE_TRADE_MULT     = 5.0    # trade > 5× avg = "whale" (threshold relativo)


def detect_whale_accumulation(coin_data: dict) -> dict:
    price         = float(coin_data.get("price", 0))
    recent_trades = coin_data.get("recent_trades", [])
    klines        = coin_data.get("klines_15m", [])

    if not price:
        return _empty_result("Sem preço")

    buy_vol = sell_vol = whale_buys = total_buy_usd = 0.0
    trade_sizes = []

    for t in recent_trades:
        try:
            qty     = float(t.get("q", 0))
            tprice  = float(t.get("p", price))
            is_sell = bool(t.get("m", False))  # m=True → taker sell
            usd     = qty * tprice
            trade_sizes.append(usd)
            if is_sell:
                sell_vol += usd
            else:
                buy_vol += usd
        except (ValueError, TypeError):
            continue

    avg_size = sum(trade_sizes) / len(trade_sizes) if trade_sizes else 0

    for t in recent_trades:
        try:
            qty     = float(t.get("q", 0))
            tprice  = float(t.get("p", price))
            is_sell = bool(t.get("m", False))
            usd     = qty * tprice
            if not is_sell and avg_size > 0 and usd >= avg_size * LARGE_TRADE_MULT:
                whale_buys    += 1
                total_buy_usd += usd
        except (ValueError, TypeError):
            continue

    total_vol           = buy_vol + sell_vol
    buy_pressure_ratio  = buy_vol / total_vol if total_vol > 0 else 0.5
    cvd_score           = _calc_cvd(klines)

    large_buy_detected  = whale_buys >= 2
    cvd_positive        = cvd_score > CVD_POSITIVE_THRESH
    whale_accumulation  = large_buy_detected or (cvd_positive and buy_pressure_ratio >= BUY_PRESSURE_HIGH)

    strength = _calc_strength(buy_pressure_ratio, cvd_score, whale_buys, total_buy_usd)

    log.debug(f"[WhalAcc] strength={strength:.0f} press={buy_pressure_ratio:.2f} cvd={cvd_score:.2f} buys={whale_buys}")

    return {
        "whale_accumulation":    whale_accumulation,
        "accumulation_strength": round(strength, 1),
        "large_buy_detected":    large_buy_detected,
        "cvd_positive":          cvd_positive,
        "buy_pressure_ratio":    round(buy_pressure_ratio, 3),
        "estimated_buy_usd":     round(total_buy_usd),
        "whale_buy_count":       whale_buys,
        "cvd_score":             round(cvd_score, 3),
        "details": {"buy_vol": round(buy_vol), "sell_vol": round(sell_vol), "total_trades": len(recent_trades)},
    }


def _calc_cvd(klines: list) -> float:
    if not klines:
        return 0.0
    cum = total = 0.0
    for k in klines[-20:]:
        try:
            o, c, v = float(k[1]), float(k[4]), float(k[5])
            cum   += v if c > o else -v
            total += v
        except (ValueError, TypeError, IndexError):
            continue
    return max(-1.0, min(1.0, cum / total)) if total else 0.0


def _calc_strength(ratio: float, cvd: float, whales: int, buy_usd: float) -> float:
    score = 0.0
    if ratio > 0.5:
        score += min(40.0, (ratio - 0.5) / 0.5 * 40)
    if cvd > 0:
        score += min(30.0, cvd * 30)
    score += min(30.0, whales * 5.0)  # até 6 whales = 30 pts
    return min(100.0, score)


def _empty_result(reason: str) -> dict:
    return {
        "whale_accumulation": False, "accumulation_strength": 0.0,
        "large_buy_detected": False, "cvd_positive": False,
        "buy_pressure_ratio": 0.5,  "estimated_buy_usd": 0.0,
        "whale_buy_count": 0,       "cvd_score": 0.0,
        "details": {"error": reason},
    }
