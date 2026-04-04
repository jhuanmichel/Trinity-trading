"""
cascade_prediction_model.py — Modelo de Predição de Cascata de Liquidações

Analisa a probabilidade e intensidade de uma cascata de liquidações:
  - Clusters de stop losses abaixo do preço (detectados via gaps de liquidez)
  - Aceleração esperada por zonas sem liquidez (gaps de preço)
  - Níveis de suporte chave que se tornaram resistência
  - Estimativa de drawdown alvo se a cascata iniciar

Lógica:
  1. Mapeia clusters de liquidação prováveis (fundos recentes, níveis psicológicos)
  2. Calcula quantidade de USD alavancada nesses clusters (via OI + L/S ratio)
  3. Identifica gaps abaixo onde não há liquidez = aceleração
  4. Estima drawdown esperado se cascade se ativar

cascade_strength [0-100]:
  - 100 → cascata extremamente provável e violenta se iniciada
  -   0 → mercado sem riscos de cascade

Saída:
  {
    "cascade_strength":      0-100,
    "stop_cluster_below":    float,   # preço com maior cluster de stops
    "gap_acceleration_risk": float,   # [0-100] risco de aceleração por gap
    "cascade_target":        float,   # preço estimado do piso da cascata
    "estimated_drawdown":    float,   # drawdown % esperado
    "key_supports_broken":   int,     # quantos suportes chave já quebraram
    "details":               dict,
  }
"""
import logging
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Limiares ──────────────────────────────────────────────────────────────────
ROUND_NUMBER_TOLERANCE  = 0.003   # 0.3% — preço "perto" de número redondo
GAP_THRESHOLD_PCT       = 0.008   # gap ≥ 0.8% entre candles = zona de vácuo
SUPPORT_LOOKBACK        = 30      # quantas velas 15m para mapear suportes
MIN_SUPPORT_BOUNCES     = 2       # mínimo 2 toques para considerar suporte
LONG_RATIO_RISK_MULT    = 1.5     # amplificador: mais longs = cascata maior
CASCADE_BASE_DRAWDOWN   = 0.04    # drawdown base esperado de 4%
MAX_DRAWDOWN_EST        = 0.18    # cap de estimativa em 18%


def predict_cascade(coin_data: dict) -> dict:
    """
    Modela a cascata de liquidações potencial.

    Args:
        coin_data: dict com "price", "low_24h", "high_24h", "open_interest",
                   "long_short_ratio", "oi_change_pct", "klines_15m",
                   e resultado do leverage_pressure_detector (opcional)

    Returns:
        dict com cascade_strength e detalhes
    """
    price            = float(coin_data.get("price", 0))
    low_24h          = float(coin_data.get("low_24h", price * 0.95))
    high_24h         = float(coin_data.get("high_24h", price * 1.05))
    open_interest    = float(coin_data.get("open_interest", 0))
    long_short_ratio = float(coin_data.get("long_short_ratio", 1.0))
    oi_change_pct    = float(coin_data.get("oi_change_pct", 0))
    klines           = coin_data.get("klines_15m", [])

    # resultado do leverage detector (se disponível)
    lev_result       = coin_data.get("_leverage_result", {})

    if not price:
        return _empty_result("Sem dados de preço")

    # ── Identificar clusters de stop abaixo do preço ──────────────────────
    stop_cluster, cluster_strength = _find_stop_cluster(
        price, low_24h, klines
    )

    # ── Gaps de liquidez abaixo do preço ──────────────────────────────────
    gap_risk = _calc_gap_acceleration_risk(klines, price)

    # ── Suportes quebrados ─────────────────────────────────────────────────
    supports_broken = _count_broken_supports(klines, price)

    # ── Estima drawdown alvo ───────────────────────────────────────────────
    cascade_target, estimated_drawdown = _estimate_cascade_target(
        price, stop_cluster, low_24h, open_interest,
        long_short_ratio, gap_risk,
    )

    # ── cascade_strength ──────────────────────────────────────────────────
    cascade_strength = _calc_cascade_strength(
        cluster_strength, gap_risk, supports_broken,
        open_interest, long_short_ratio, oi_change_pct,
        lev_result.get("cascade_probability", 0),
    )

    log.debug(
        f"[Cascade] strength={cascade_strength:.0f} | cluster@{stop_cluster:.2f} "
        f"gap_risk={gap_risk:.0f} supports_broken={supports_broken} "
        f"target={cascade_target:.2f} dd={estimated_drawdown*100:.1f}%"
    )

    return {
        "cascade_strength":      round(cascade_strength, 1),
        "stop_cluster_below":    round(stop_cluster, 4),
        "gap_acceleration_risk": round(gap_risk, 1),
        "cascade_target":        round(cascade_target, 4),
        "estimated_drawdown":    round(estimated_drawdown * 100, 2),
        "key_supports_broken":   supports_broken,
        "details": {
            "cluster_strength":  round(cluster_strength, 1),
            "open_interest_usd": round(open_interest),
            "long_short_ratio":  round(long_short_ratio, 2),
            "oi_change_pct":     round(oi_change_pct, 2),
            "low_24h":           round(low_24h, 4),
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_stop_cluster(
    price: float, low_24h: float, klines: list
) -> Tuple[float, float]:
    """
    Identifica o nível mais provável de cluster de stops abaixo do preço.

    Estratégia:
    1. Mínimos das últimas N velas (igual stops de compradores)
    2. Números redondos psicológicos abaixo do preço
    3. Mínimo de 24h (zona de stops mais óbvia)

    Retorna: (nível_do_cluster, força_do_cluster 0-100)
    """
    candidates = []

    # 1. Mínimo 24h — zona mais óbvia de stops
    if low_24h < price:
        dist_pct = (price - low_24h) / price
        weight   = 40.0 * (1 - min(1.0, dist_pct / 0.05))  # mais perto = mais peso
        candidates.append((low_24h, weight))

    # 2. Mínimos locais das velas (suportes recentes)
    if klines:
        lows = [float(k[3]) for k in klines[-SUPPORT_LOOKBACK:]]
        for lo in lows:
            if lo < price * 0.999:  # pelo menos 0.1% abaixo
                dist_pct = (price - lo) / price
                weight   = 20.0 * (1 - min(1.0, dist_pct / 0.08))
                candidates.append((lo, max(0, weight)))

    # 3. Números redondos psicológicos abaixo
    round_candidates = _get_round_numbers_below(price)
    for rnd in round_candidates:
        dist_pct = (price - rnd) / price
        if 0 < dist_pct < 0.10:  # dentro de 10% abaixo
            candidates.append((rnd, 15.0 * (1 - dist_pct / 0.10)))

    if not candidates:
        return price * 0.96, 20.0

    # Agrupa candidatos próximos (±1%)
    clusters: dict = {}
    for level, weight in candidates:
        key = round(level / (price * 0.01)) * (price * 0.01)
        clusters[key] = clusters.get(key, 0) + weight

    # Escolhe o cluster com maior peso
    best_level  = max(clusters, key=clusters.get)
    best_weight = min(100.0, clusters[best_level])

    return best_level, best_weight


def _get_round_numbers_below(price: float) -> List[float]:
    """Retorna números redondos psicológicos abaixo do preço."""
    rounds = []
    # Determina a magnitude do preço
    magnitude = 10 ** (len(str(int(price))) - 2)  # ex: price=150 → mag=10
    magnitude = max(1, magnitude)

    # Gera múltiplos de magnitude abaixo do preço
    base = int(price / magnitude) * magnitude
    for mult in range(1, 5):
        level = base - mult * magnitude
        if level > 0:
            rounds.append(float(level))
    return rounds


def _calc_gap_acceleration_risk(klines: list, price: float) -> float:
    """
    Calcula risco de aceleração por gaps de preço [0-100].

    Gaps onde não houve negociação = queda sem suporte → velocidade 2-3×.
    """
    if len(klines) < 5:
        return 20.0

    # Verifica gaps entre lows e highs consecutivos abaixo do preço
    total_gap_pct = 0.0
    gap_count     = 0

    candles_below = [k for k in klines[-SUPPORT_LOOKBACK:] if float(k[4]) < price]

    for i in range(len(candles_below) - 1):
        high_next = float(candles_below[i + 1][2])
        low_curr  = float(candles_below[i][3])
        if low_curr > high_next:  # gap abaixo
            gap_pct = (low_curr - high_next) / price
            if gap_pct > GAP_THRESHOLD_PCT:
                total_gap_pct += gap_pct
                gap_count     += 1

    if gap_count == 0:
        return 10.0

    risk = min(100.0, gap_count * 15 + (total_gap_pct / GAP_THRESHOLD_PCT) * 5)
    return risk


def _count_broken_supports(klines: list, price: float) -> int:
    """Conta quantos suportes chave recentes foram quebrados."""
    if len(klines) < 10:
        return 0

    # Encontra mínimos locais (suportes) que viraram resistência
    lows     = [float(k[3]) for k in klines]
    broken   = 0
    supports = []

    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            if lows[i] < lows[i - 2] and lows[i] < lows[i + 2]:
                supports.append(lows[i])

    # Conta suportes abaixo do preço atual que antes eram acima
    for s in supports:
        if s > price * 0.98:  # suporte recentemente quebrado
            broken += 1

    return min(broken, 5)


def _estimate_cascade_target(
    price: float,
    stop_cluster: float,
    low_24h: float,
    open_interest: float,
    ls_ratio: float,
    gap_risk: float,
) -> Tuple[float, float]:
    """
    Estima preço alvo e drawdown se cascade se ativar.

    Cascade target = stop_cluster - (aceleração por gap)
    """
    # Distância ao cluster de stops
    stop_dist = (price - stop_cluster) / price

    # Aceleração por gaps: até 2× a distância inicial
    gap_multiplier = 1.0 + (gap_risk / 100) * 1.0

    # Amplificação por excesso de longs alavancados
    ls_amplifier = 1.0 + max(0, (ls_ratio - 1.0) * 0.3)

    # Drawdown total estimado
    raw_drawdown = stop_dist * gap_multiplier * ls_amplifier

    # OI alto = mais combustível para a cascata
    if open_interest > 1_000_000_000:  # > $1B OI
        raw_drawdown *= 1.15

    estimated_drawdown = min(MAX_DRAWDOWN_EST, max(CASCADE_BASE_DRAWDOWN, raw_drawdown))
    cascade_target     = price * (1 - estimated_drawdown)

    return cascade_target, estimated_drawdown


def _calc_cascade_strength(
    cluster_strength: float,
    gap_risk: float,
    supports_broken: int,
    open_interest: float,
    ls_ratio: float,
    oi_change_pct: float,
    lev_cascade_prob: float,
) -> float:
    """Calcula cascade_strength [0-100]."""
    score = 0.0

    # Fator 1: Força do cluster de stops (0-30 pts)
    score += cluster_strength * 0.30

    # Fator 2: Risco de gap (0-25 pts)
    score += gap_risk * 0.25

    # Fator 3: Suportes quebrados (0-15 pts)
    score += min(15.0, supports_broken * 5.0)

    # Fator 4: Leverage pressure (0-20 pts — input externo)
    score += lev_cascade_prob * 0.20

    # Fator 5: OI alto + ls_ratio desfavorável (0-10 pts bônus)
    if open_interest > 500_000_000 and ls_ratio > 1.5:
        score += 10.0
    elif open_interest > 200_000_000 and ls_ratio > 1.3:
        score += 5.0

    return min(100.0, score)


def _empty_result(reason: str) -> dict:
    return {
        "cascade_strength":      0.0,
        "stop_cluster_below":    0.0,
        "gap_acceleration_risk": 0.0,
        "cascade_target":        0.0,
        "estimated_drawdown":    0.0,
        "key_supports_broken":   0,
        "details":               {"error": reason},
    }
