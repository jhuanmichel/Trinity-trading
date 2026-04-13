"""
liquidity_gravity_detector.py — Detector de Gravidade de Liquidez (Cap. 20)

Detecta clusters de liquidez acima do preço que funcionam como "ímã":
  - Muitos shorts com stops acima = price magnet (squeeze target)
  - Bid walls próximas ao preço = suporte forte = rampa de lançamento
  - Ask walls finas acima = pouca resistência = caminho limpo para subida

Lógica:
  1. Analisa orderbook: bids (suporte) e asks (resistência) em bandas de preço
  2. Detecta clusters de liquidez acima (stop hunts, short squeeze targets)
  3. Calcula força do "efeito ímã" — quão provável o preço será atraído para cima

Saída:
  {
    "liquidity_gravity":   "UP" | "NEUTRAL" | "DOWN",
    "target_price":        float,       # preço alvo do cluster de liquidez
    "strength":            0-100,       # força do efeito ímã
    "bid_wall_support":    bool,        # bid wall forte próxima ao preço
    "ask_cluster_above":   bool,        # cluster de asks acima = stops de shorts
    "bid_ask_ratio":       float,       # bids/asks nas bandas analisadas
    "clear_path_pct":      float,       # % de caminho limpo até o cluster
    "details":             dict,
  }
"""
import logging

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BID_WALL_BAND_PCT    = 2.0    # analisa bids até 2.0% abaixo do preço
ASK_BAND_PCT         = 5.0    # analisa asks até 5% acima do preço
BID_WALL_RATIO       = 2.0    # bids/asks > 2.0 = bid wall forte (MEXC altcoins)
ASK_CLUSTER_BAND_PCT = 4.0    # cluster de asks entre 1% e 4% acima
MIN_CLUSTER_USD      = 2_000  # cluster mínimo ($2K — permite altcoins com book fino)
GRAVITY_STRONG       = 60.0   # strength >= 60 = efeito ímã forte (calibrado MEXC)
PRICE_BAND_PCT       = 0.005  # 0.5% por banda de análise


def detect_liquidity_gravity(coin_data: dict) -> dict:
    price    = float(coin_data.get("price", 0))
    orderbook = coin_data.get("orderbook", {})

    if not price or not orderbook:
        return _empty_result("Sem orderbook")

    bids = orderbook.get("bids", [])  # [[price, qty], ...]
    asks = orderbook.get("asks", [])

    if not bids or not asks:
        return _empty_result("Orderbook vazio")

    try:
        bids_parsed = [(float(p), float(q)) for p, q in bids]
        asks_parsed = [(float(p), float(q)) for p, q in asks]
    except (ValueError, TypeError):
        return _empty_result("Orderbook inválido")

    # ── Análise de bids (suporte) ──────────────────────────────────────────
    bid_floor    = price * (1 - BID_WALL_BAND_PCT / 100)
    bids_near    = [(p, q) for p, q in bids_parsed if p >= bid_floor]
    bid_usd_near = sum(p * q for p, q in bids_near)

    # ── Análise de asks (resistência / clusters acima) ─────────────────────
    ask_ceil     = price * (1 + ASK_BAND_PCT / 100)
    asks_above   = [(p, q) for p, q in asks_parsed if p <= ask_ceil]
    ask_usd_above = sum(p * q for p, q in asks_above)

    # Cluster de asks entre 1% e 5% — onde ficam os stops de shorts
    ask_cluster_floor = price * 1.01
    ask_cluster_ceil  = price * (1 + ASK_CLUSTER_BAND_PCT / 100)
    cluster_asks      = [(p, q) for p, q in asks_parsed
                         if ask_cluster_floor <= p <= ask_cluster_ceil]
    cluster_usd       = sum(p * q for p, q in cluster_asks)

    # Target = preço do maior cluster de asks acima
    target_price = _find_target_price(asks_parsed, price)

    # ── Ratio bids/asks na banda de análise ───────────────────────────────
    bid_ask_ratio = bid_usd_near / ask_usd_above if ask_usd_above > 0 else 5.0

    # ── Caminho limpo até o cluster ────────────────────────────────────────
    # Poucas asks imediatas (< 1%) = caminho limpo
    asks_immediate = [(p, q) for p, q in asks_parsed
                      if p <= price * 1.01]
    immediate_usd  = sum(p * q for p, q in asks_immediate)
    clear_path_pct = max(0.0, 100.0 - min(100.0, immediate_usd / 10_000))

    # ── Flags ──────────────────────────────────────────────────────────────
    bid_wall_support  = bid_ask_ratio >= BID_WALL_RATIO and bid_usd_near >= MIN_CLUSTER_USD
    ask_cluster_above = cluster_usd >= MIN_CLUSTER_USD  # shorts presos com stops acima

    # ── Score de força ─────────────────────────────────────────────────────
    strength = _calc_gravity_strength(
        bid_ask_ratio, bid_wall_support, ask_cluster_above,
        cluster_usd, clear_path_pct, target_price, price,
    )

    # ── Direção ────────────────────────────────────────────────────────────
    if strength >= 40 and (bid_wall_support or ask_cluster_above):
        gravity = "UP"
    elif bid_ask_ratio < 0.5:
        gravity = "DOWN"
    else:
        gravity = "NEUTRAL"

    log.debug(
        f"[LiqGrav] gravity={gravity} strength={strength:.0f} "
        f"bid/ask={bid_ask_ratio:.2f} cluster=${cluster_usd/1e3:.0f}K "
        f"target={target_price:.4f}"
    )

    return {
        "liquidity_gravity": gravity,
        "target_price":      round(target_price, 6),
        "strength":          round(strength, 1),
        "bid_wall_support":  bid_wall_support,
        "ask_cluster_above": ask_cluster_above,
        "bid_ask_ratio":     round(bid_ask_ratio, 2),
        "clear_path_pct":    round(clear_path_pct, 1),
        "details": {
            "bid_usd_near":    round(bid_usd_near),
            "ask_usd_above":   round(ask_usd_above),
            "cluster_usd":     round(cluster_usd),
            "immediate_resistance_usd": round(immediate_usd),
        },
    }


def _find_target_price(asks: list, price: float) -> float:
    """
    Encontra o preço do maior cluster de asks acima do preço atual.
    Usa bandas de 0.5% para agrupar níveis.
    """
    if not asks:
        return price * 1.03  # default: 3% acima

    # Agrupa asks em bandas de 0.5%
    clusters: dict = {}
    for ask_price, qty in asks:
        if ask_price <= price:
            continue
        band = round(ask_price / (price * PRICE_BAND_PCT)) * (price * PRICE_BAND_PCT)
        usd  = ask_price * qty
        clusters[band] = clusters.get(band, 0) + usd

    if not clusters:
        return price * 1.03

    # Target = banda com maior liquidez
    target_band = max(clusters, key=clusters.get)
    return target_band


def _calc_gravity_strength(
    bid_ask_ratio: float,
    bid_wall: bool,
    ask_cluster: bool,
    cluster_usd: float,
    clear_path: float,
    target: float,
    price: float,
) -> float:
    score = 0.0

    # Fator 1: Ratio bids/asks (0-30 pts) — suporte vs resistência
    if bid_ask_ratio >= BID_WALL_RATIO:
        score += 30.0
    elif bid_ask_ratio >= 1.3:
        score += min(30.0, (bid_ask_ratio / BID_WALL_RATIO) * 20)

    # Fator 2: Bid wall de suporte (0-20 pts)
    if bid_wall:
        score += 20.0

    # Fator 3: Cluster de asks = stops de shorts como ímã (0-25 pts)
    if ask_cluster:
        cluster_k = cluster_usd / 1_000     # em K (escala MEXC altcoins)
        score += min(25.0, cluster_k * 2.5)

    # Fator 4: Caminho limpo até o cluster (0-15 pts)
    score += clear_path * 0.15

    # Fator 5: Target próximo = pull forte (bônus 0-10 pts)
    if target > price:
        distance_pct = (target - price) / price * 100
        if distance_pct <= 2.0:
            score += 10.0  # muito próximo = pull imediato
        elif distance_pct <= 4.0:
            score += 5.0

    return min(100.0, score)


def _empty_result(reason: str) -> dict:
    return {
        "liquidity_gravity": "NEUTRAL",
        "target_price":      0.0,
        "strength":          0.0,
        "bid_wall_support":  False,
        "ask_cluster_above": False,
        "bid_ask_ratio":     1.0,
        "clear_path_pct":    0.0,
        "details":           {"error": reason},
    }
