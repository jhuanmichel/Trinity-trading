"""
liquidity_collapse_detector.py — Detector de Colapso de Liquidez

Analisa o orderbook para detectar sinais de colapso iminente:
  - Livro de ordens fino (pouca profundidade total)
  - Desaparecimento de suporte de compra (bids enfraquecendo)
  - Muros de venda acima do preço atual
  - Desequilíbrio extremo bid/ask
  - Gaps de liquidez (regiões sem ordens)

Lógica:
  bid_depth_usd   = soma total das bids nos primeiros N% abaixo do preço
  ask_depth_usd   = soma total das asks nos primeiros N% acima do preço
  imbalance       = (bid_depth - ask_depth) / (bid_depth + ask_depth)
  thin_book       = True se total_depth < THIN_BOOK_USD_THRESHOLD
  ask_wall        = True se existe ask >= ASK_WALL_MULTIPLIER * bid_depth_usd / 2

Severity [0-100]:
  - 100 → livro completamente colapsado (bids desaparecem + ask wall enorme)
  -   0 → liquidez saudável e equilibrada

Saída:
  {
    "severity":              0-100,
    "thin_book":             bool,
    "bid_support_collapse":  bool,
    "ask_wall_detected":     bool,
    "bid_ask_imbalance":     float,   # [-1, +1] negativo = mais asks
    "bid_depth_usd":         float,   # profundidade bids em USD
    "ask_depth_usd":         float,   # profundidade asks em USD
    "depth_score":           float,   # score de profundidade [0-100]
    "liquidity_gap_pct":     float,   # maior gap no orderbook em %
    "details":               dict,
  }
"""
import logging
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)

# ── Limiares ──────────────────────────────────────────────────────────────────
DEPTH_BAND_PCT         = 0.03    # analisa ±3% em torno do preço
THIN_BOOK_USD          = 50_000   # livro "fino" abaixo de $50K (Gate.io altcoins)
COLLAPSE_BID_USD       = 20_000   # suporte colapsado abaixo de $20K
ASK_WALL_MULTIPLIER    = 3.0      # ask wall se asks > 3× bids
IMBALANCE_THRESHOLD    = -0.35   # imbalance ≤ -0.35 = muito mais asks que bids
GAP_THRESHOLD_PCT      = 0.008   # gap ≥ 0.8% entre níveis = gap relevante


def detect_liquidity_collapse(coin_data: dict) -> dict:
    """
    Detecta colapso de liquidez a partir do orderbook.

    Args:
        coin_data: dict com keys "price" e "orderbook" {"bids": [...], "asks": [...]}

    Returns:
        dict com severity e detalhes
    """
    price     = float(coin_data.get("price", 0))
    orderbook = coin_data.get("orderbook", {})
    raw_bids  = orderbook.get("bids", [])
    raw_asks  = orderbook.get("asks", [])

    if not price or not raw_bids or not raw_asks:
        return _empty_result("Sem dados de orderbook")

    try:
        bids = [(float(p), float(q)) for p, q in raw_bids]
        asks = [(float(p), float(q)) for p, q in raw_asks]
    except (ValueError, TypeError):
        return _empty_result("Formato de orderbook inválido")

    # ── Filtra dentro da banda ±3% ─────────────────────────────────────────
    band_low  = price * (1 - DEPTH_BAND_PCT)
    band_high = price * (1 + DEPTH_BAND_PCT)

    near_bids = [(p, q) for p, q in bids if p >= band_low]
    near_asks = [(p, q) for p, q in asks if p <= band_high]

    bid_depth_usd = sum(p * q for p, q in near_bids)
    ask_depth_usd = sum(p * q for p, q in near_asks)
    total_depth   = bid_depth_usd + ask_depth_usd

    # ── Indicadores binários ───────────────────────────────────────────────
    thin_book             = total_depth < THIN_BOOK_USD
    bid_support_collapse  = bid_depth_usd < COLLAPSE_BID_USD
    ask_wall_detected     = (
        ask_depth_usd > ASK_WALL_MULTIPLIER * bid_depth_usd
        if bid_depth_usd > 0 else False
    )

    # ── Imbalance ─────────────────────────────────────────────────────────
    if total_depth > 0:
        bid_ask_imbalance = (bid_depth_usd - ask_depth_usd) / total_depth
    else:
        bid_ask_imbalance = 0.0

    # ── Gap de liquidez ───────────────────────────────────────────────────
    liquidity_gap_pct = _find_largest_gap(
        [p for p, _ in near_bids] + [p for p, _ in near_asks], price
    )

    # ── Depth score [0-100] (mais fundo = melhor = menor severidade) ───────
    depth_score = min(100.0, (bid_depth_usd / max(THIN_BOOK_USD, 1)) * 50)

    # ── Severity [0-100] ──────────────────────────────────────────────────
    severity = _calc_severity(
        bid_depth_usd, ask_depth_usd, total_depth,
        bid_ask_imbalance, thin_book, bid_support_collapse,
        ask_wall_detected, liquidity_gap_pct,
    )

    log.debug(
        f"[LiqCollapse] severity={severity:.0f} | bid={bid_depth_usd/1e3:.0f}K "
        f"ask={ask_depth_usd/1e3:.0f}K | imbal={bid_ask_imbalance:.2f} | "
        f"thin={thin_book} wall={ask_wall_detected}"
    )

    return {
        "severity":             round(severity, 1),
        "thin_book":            thin_book,
        "bid_support_collapse": bid_support_collapse,
        "ask_wall_detected":    ask_wall_detected,
        "bid_ask_imbalance":    round(bid_ask_imbalance, 3),
        "bid_depth_usd":        round(bid_depth_usd),
        "ask_depth_usd":        round(ask_depth_usd),
        "depth_score":          round(depth_score, 1),
        "liquidity_gap_pct":    round(liquidity_gap_pct * 100, 3),
        "details": {
            "total_depth_usd": round(total_depth),
            "band_pct":        DEPTH_BAND_PCT * 100,
            "near_bid_levels": len(near_bids),
            "near_ask_levels": len(near_asks),
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc_severity(
    bid_usd: float, ask_usd: float, total_usd: float,
    imbalance: float, thin: bool, collapse: bool,
    wall: bool, gap_pct: float,
) -> float:
    """Calcula severity 0-100 com base em múltiplos fatores."""
    score = 0.0

    # Fator 1: Livro fino (0-30 pts)
    if total_usd > 0:
        thinness = max(0.0, 1.0 - total_usd / (THIN_BOOK_USD * 5))
        score += thinness * 30

    # Fator 2: Suporte de compra fraco (0-30 pts)
    if bid_usd > 0:
        bid_weak = max(0.0, 1.0 - bid_usd / (COLLAPSE_BID_USD * 3))
        score += bid_weak * 30

    # Fator 3: Imbalance negativo (0-20 pts)
    if imbalance < 0:
        score += min(20.0, abs(imbalance) * 40)

    # Fator 4: Muro de venda (0-15 pts)
    if wall:
        score += 15.0

    # Fator 5: Gap de liquidez (0-5 pts)
    if gap_pct > GAP_THRESHOLD_PCT:
        score += min(5.0, (gap_pct / GAP_THRESHOLD_PCT) * 2.5)

    return min(100.0, score)


def _find_largest_gap(prices: List[float], ref_price: float) -> float:
    """Encontra o maior gap percentual entre níveis de preço consecutivos."""
    if len(prices) < 2:
        return 0.0
    sorted_prices = sorted(prices)
    max_gap = 0.0
    for i in range(len(sorted_prices) - 1):
        gap = (sorted_prices[i + 1] - sorted_prices[i]) / ref_price
        if gap > max_gap:
            max_gap = gap
    return max_gap


def _empty_result(reason: str) -> dict:
    return {
        "severity":             0.0,
        "thin_book":            False,
        "bid_support_collapse": False,
        "ask_wall_detected":    False,
        "bid_ask_imbalance":    0.0,
        "bid_depth_usd":        0,
        "ask_depth_usd":        0,
        "depth_score":          50.0,
        "liquidity_gap_pct":    0.0,
        "details":              {"error": reason},
    }
