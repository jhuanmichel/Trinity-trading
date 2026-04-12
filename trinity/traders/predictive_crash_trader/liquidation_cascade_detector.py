"""
liquidation_cascade_detector.py — Detector de Cascata de Liquidação (Gate.io)

Combina sinais de leverage_pressure_detector e cascade_prediction_model para
produzir um score único 0-25 representando o risco de cascata imediata.

Score 0-25:
  liq_acceleration (0-10) → probabilidade de cascade por alavancagem + aceleração de gap
  oi_leverage      (0-8)  → OI alto × funding extremo = combustível de cascata
  stop_cluster     (0-7)  → clusters de stops presos abaixo do preço = gatilho

Depende de:
  coin_data["_leverage_result"] → dict de detect_leverage_pressure()
  coin_data["_cascade_result"]  → dict de predict_cascade()

Saída:
  {
    "score":            0-25,    # score total de risco de cascata
    "liq_acceleration": 0-10,
    "oi_leverage":      0-8,
    "stop_cluster":     0-7,
    "cascade_strength": float,   # cascade_strength do model (0-100)
    "cascade_ready":    bool,    # True se score >= CASCADE_READY_THRESH
  }
"""
import logging

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
OI_HIGH_THRESHOLD     = 50_000_000    # OI > $50M = alto (Gate.io altcoins)
OI_EXTREME_THRESHOLD  = 200_000_000   # OI > $200M = extremo
FUNDING_HIGH          = 0.0004        # funding > 0.04%/8h = alto
FUNDING_EXTREME       = 0.0008        # funding > 0.08%/8h = extremo
LS_LONG_HEAVY         = 1.7           # L/S > 1.7 = longs sobrecarregados
CASCADE_READY_THRESH  = 12            # score >= 12 = cascata em preparação


def detect_liquidation_cascade(coin_data: dict) -> dict:
    """
    Detecta risco de cascata de liquidações usando resultados pré-computados.

    Args:
        coin_data: dict com "_leverage_result" e "_cascade_result" injetados
                   pelo orquestrador, mais "open_interest" e "funding_rate"

    Returns:
        dict com score (0-25) e detalhes
    """
    lev_result = coin_data.get("_leverage_result", {})
    cas_result = coin_data.get("_cascade_result",  {})

    # Fallback: dados diretos do coin_data se os resultados não estiverem disponíveis
    funding_rate    = float(lev_result.get("funding_rate",    coin_data.get("funding_rate", 0.0)))
    oi_change_pct   = float(lev_result.get("oi_change_pct",   coin_data.get("oi_change_pct", 0.0)))
    ls_ratio        = float(lev_result.get("long_short_ratio", coin_data.get("long_short_ratio", 1.0)))
    oi_usd          = float(coin_data.get("open_interest", 0.0))

    cascade_prob    = float(lev_result.get("cascade_probability",  0.0))
    gap_accel       = float(cas_result.get("gap_acceleration_risk", 0.0))
    cascade_str     = float(cas_result.get("cascade_strength",      0.0))
    key_sup_broken  = int(cas_result.get("key_supports_broken",     0))
    oi_spike        = bool(lev_result.get("oi_spike",              False))
    funding_extreme = bool(lev_result.get("funding_extreme",       False))
    long_heavy      = bool(lev_result.get("long_heavy",            False))
    oi_price_div    = bool(lev_result.get("oi_price_divergence",   False))

    # ── Componente 1: Liq Acceleration (0-10) ─────────────────────────────
    liq_acc = _calc_liq_acceleration(cascade_prob, gap_accel, key_sup_broken)

    # ── Componente 2: OI × Leverage (0-8) ─────────────────────────────────
    oi_lev = _calc_oi_leverage(
        oi_usd, funding_rate, ls_ratio,
        oi_spike, funding_extreme, long_heavy, oi_price_div,
    )

    # ── Componente 3: Stop Cluster (0-7) ──────────────────────────────────
    stop_cl = _calc_stop_cluster(cascade_str, cas_result)

    total = round(liq_acc + oi_lev + stop_cl, 2)
    total = min(25.0, total)

    cascade_ready = total >= CASCADE_READY_THRESH

    log.debug(
        f"[LiqCascade] score={total:.1f}/25 "
        f"liq_acc={liq_acc:.1f} oi_lev={oi_lev:.1f} stop_cl={stop_cl:.1f} "
        f"cas_str={cascade_str:.0f} cascade_prob={cascade_prob:.0f}"
    )

    return {
        "score":            total,
        "liq_acceleration": round(liq_acc, 2),
        "oi_leverage":      round(oi_lev, 2),
        "stop_cluster":     round(stop_cl, 2),
        "cascade_strength": cascade_str,
        "cascade_ready":    cascade_ready,
    }


# ── Cálculos internos ─────────────────────────────────────────────────────────

def _calc_liq_acceleration(
    cascade_prob: float,
    gap_accel_risk: float,
    key_supports_broken: int,
) -> float:
    """
    Risco de aceleração de liquidações.

    cascade_prob (0-100): probabilidade de cascade da leverage_pressure_detector
    gap_accel_risk (0-100): risco de aceleração por gap do cascade_prediction_model
    key_supports_broken: quantos suportes chave já quebraram

    Score 0-10 pts.
    """
    # Combina cascade_prob e gap_accel com pesos iguais
    combined = (cascade_prob * 0.5 + gap_accel_risk * 0.5) / 100.0  # [0, 1]
    score    = combined * 8.0  # até 8 pts pela probabilidade

    # Bônus por suportes quebrados: cada suporte +1 pt (max +2 pts)
    score += min(2.0, key_supports_broken * 1.0)

    return min(10.0, score)


def _calc_oi_leverage(
    oi_usd: float,
    funding_rate: float,
    ls_ratio: float,
    oi_spike: bool,
    funding_extreme: bool,
    long_heavy: bool,
    oi_price_div: bool,
) -> float:
    """
    Combustível de cascata: OI alto × funding extremo × longs sobrecarregados.

    Score 0-8 pts.
    """
    score = 0.0

    # OI alto = muito combustível de liquidação
    if oi_usd >= OI_EXTREME_THRESHOLD:
        score += 3.0
    elif oi_usd >= OI_HIGH_THRESHOLD:
        score += min(3.0, (oi_usd / OI_EXTREME_THRESHOLD) * 3.0)

    # Funding alto = longs sobreexpostos ao custo de funding
    if funding_rate >= FUNDING_EXTREME:
        score += 2.5
    elif funding_rate >= FUNDING_HIGH:
        ratio = (funding_rate - FUNDING_HIGH) / (FUNDING_EXTREME - FUNDING_HIGH)
        score += 1.0 + ratio * 1.5

    # Longs sobrecarregados (L/S > 1.7) = alvo fácil para cascata
    if ls_ratio >= LS_LONG_HEAVY:
        score += min(1.5, (ls_ratio - LS_LONG_HEAVY) * 1.0)

    # Flags booleanos como aceleradores
    if oi_spike:
        score += 0.5
    if oi_price_div:
        score += 0.5

    return min(8.0, score)


def _calc_stop_cluster(cascade_str: float, cas_result: dict) -> float:
    """
    Clusters de stops presos abaixo do preço = gatilho de liquidação.

    cascade_strength (0-100): do cascade_prediction_model
    cas_result["gap_acceleration_risk"]: risco de aceleração

    Score 0-7 pts.
    """
    if cascade_str <= 0:
        return 0.0

    # Base: cascade_strength indica densidade de stops
    base = (cascade_str / 100.0) * 5.0  # até 5 pts

    # Bônus se drawdown estimado é significativo (> 5%)
    estimated_dd = float(cas_result.get("estimated_drawdown", 0.0))
    if estimated_dd >= 8.0:
        base += 2.0
    elif estimated_dd >= 5.0:
        base += 1.0

    return min(7.0, base)
