"""
crash_scoring_engine.py — Motor de Score de Crash (Cap. 19) — v2 Institutional

Combina todos os detectores com pesos institucionais + Expected Move Model.

Filtro institucional: só retorna oportunidades com expected_move >= 8%

crash_score [0-100]:
  0  - 40  → LOW    (mercado saudável, sem sinais)
  40 - 60  → MEDIUM (sinais emergentes, atenção)
  60 - 80  → HIGH   (múltiplos sinais alinhados, precaução)
  80 - 100 → EXTREME (crash iminente, ação imediata)

Expected Move Classification:
  MICRO     → < 8%   (filtrado — micro movimento)
  WEAK      → 8-12%  (movimento mínimo tradeable)
  TRADEABLE → 12-18% (oportunidade real)
  STRONG    → 18-25% (oportunidade forte)
  EXTREME   → 25%+   (evento de liquidação em cascata)

Opportunity Score = Expected_Move×0.35 + Volatility×0.25 + Liquidity×0.20 + Squeeze×0.20

Saída:
  {
    "crash_score":          0-100,
    "crash_probability":    "LOW" | "MEDIUM" | "HIGH" | "EXTREME",
    "urgency":              "WATCH" | "ALERT" | "DANGER" | "CRITICAL",
    "recommended_action":   str,
    "signal_valid":         bool,
    "component_scores":     dict,
    "top_signals":          list[str],
    "expected_move_pct":    float,   # movimento esperado em %
    "move_classification":  str,     # MICRO/WEAK/TRADEABLE/STRONG/EXTREME
    "tradeable":            bool,    # expected_move >= 8%
    "opportunity_score":    float,   # score composto 0-100
  }
"""
import logging
from typing import Dict

log = logging.getLogger(__name__)

# ── Pesos do modelo ────────────────────────────────────────────────────────────
WEIGHTS = {
    "liquidity":   0.25,
    "leverage":    0.20,
    "whale":       0.20,
    "compression": 0.15,
    "funding_oi":  0.20,
}

# ── Limiares de classificação ──────────────────────────────────────────────────
SCORE_THRESHOLDS = {
    "EXTREME": 80,
    "HIGH":    60,
    "MEDIUM":  40,
    "LOW":     0,
}

URGENCY_MAP = {
    "EXTREME": "CRITICAL",
    "HIGH":    "DANGER",
    "MEDIUM":  "ALERT",
    "LOW":     "WATCH",
}

ACTIONS = {
    "CRITICAL": "SHORT imediato — cascata iminente, sair de longs agora",
    "DANGER":   "Fechar longs, considerar short com confirmação",
    "ALERT":    "Reduzir exposição, monitorar próximos 10min",
    "WATCH":    "Manter vigilância — sinais emergentes",
}

MIN_SCORE_VALID   = 50.0   # score mínimo para sinal "válido"
MIN_COMPONENTS    = 2      # mínimo de 2 componentes acima de 50 para validar


def score_crash(
    liquidity_result:    dict,
    leverage_result:     dict,
    whale_result:        dict,
    compression_result:  dict,
    cascade_result:      dict,
    coin_data:           dict = None,
) -> dict:
    """
    Combina todos os detectores em um score de crash unificado.

    Args:
        liquidity_result:   saída de LiquidityCollapseDetector
        leverage_result:    saída de LeveragePressureDetector
        whale_result:       saída de WhaleDumpDetector
        compression_result: saída de VolatilityCompressionDetector
        cascade_result:     saída de CascadePredictionModel

    Returns:
        dict com crash_score, probabilidade, urgência e ação recomendada
    """
    # ── Extrai scores individuais ─────────────────────────────────────────
    liq_score  = float(liquidity_result.get("severity", 0))
    lev_score  = float(leverage_result.get("cascade_probability", 0))
    whl_score  = float(whale_result.get("risk", 0))
    comp_score = float(compression_result.get("breakout_probability", 0))

    # Funding+OI Divergence — score separado com componentes do leverage
    funding_oi_score = _calc_funding_oi_score(leverage_result)

    # Cascade amplifica o sinal (bônus de até 15 pts)
    cascade_bonus = float(cascade_result.get("cascade_strength", 0)) * 0.15

    # ── Score ponderado ───────────────────────────────────────────────────
    weighted = (
        liq_score  * WEIGHTS["liquidity"]   +
        lev_score  * WEIGHTS["leverage"]    +
        whl_score  * WEIGHTS["whale"]       +
        comp_score * WEIGHTS["compression"] +
        funding_oi_score * WEIGHTS["funding_oi"]
    )

    crash_score = min(100.0, weighted + cascade_bonus)

    # ── Classificação ─────────────────────────────────────────────────────
    crash_probability = _classify_score(crash_score)
    urgency           = URGENCY_MAP[crash_probability]
    recommended_action = ACTIONS[urgency]

    # ── Validação do sinal ────────────────────────────────────────────────
    component_scores = {
        "liquidity":   round(liq_score, 1),
        "leverage":    round(lev_score, 1),
        "whale":       round(whl_score, 1),
        "compression": round(comp_score, 1),
        "funding_oi":  round(funding_oi_score, 1),
        "cascade":     round(float(cascade_result.get("cascade_strength", 0)), 1),
    }

    components_above_50 = sum(1 for v in component_scores.values() if v >= 50)
    signal_valid = crash_score >= MIN_SCORE_VALID and components_above_50 >= MIN_COMPONENTS

    # ── Top sinais detectados ─────────────────────────────────────────────
    top_signals = _extract_top_signals(
        liquidity_result, leverage_result,
        whale_result, compression_result, cascade_result,
    )

    # ── Expected Move Model ───────────────────────────────────────────────
    _cd       = coin_data or {}
    price     = float(_cd.get("price", 0))
    high_24h  = float(_cd.get("high_24h", price * 1.05))
    low_24h   = float(_cd.get("low_24h",  price * 0.95))
    ls_ratio  = float(_cd.get("long_short_ratio", 1.0))

    daily_range_pct = ((high_24h - low_24h) / price * 100) if price > 0 else 10.0

    # Base: cascade estimated_drawdown (já em %)
    base_dd = float(cascade_result.get("estimated_drawdown", 0))

    # Multiplicador de volatilidade pelo range diário
    if   daily_range_pct > 20: vol_mult = 1.6
    elif daily_range_pct > 12: vol_mult = 1.3
    elif daily_range_pct >  7: vol_mult = 1.1
    else:                      vol_mult = 1.0

    # Amplificador por excesso de longs alavancados
    lev_amp = 1.0 + max(0.0, (ls_ratio - 1.5) * 0.25)

    expected_move_pct = base_dd * vol_mult * lev_amp

    # Floor: se cascata fraca mas mercado volátil, usa range diário como base
    if base_dd < 4.0 and daily_range_pct > 8.0:
        expected_move_pct = max(expected_move_pct, daily_range_pct * 0.55)

    expected_move_pct   = round(min(35.0, expected_move_pct), 1)
    move_classification = _classify_expected_move(expected_move_pct)
    tradeable           = expected_move_pct >= 8.0

    # ── Opportunity Score ─────────────────────────────────────────────────
    move_score     = min(100.0, expected_move_pct * 4.0)   # 25% → 100
    vol_score_opp  = float(compression_result.get("breakout_probability", 0))
    liq_score_opp  = float(liquidity_result.get("severity", 0))
    sqz_score_opp  = float(leverage_result.get("cascade_probability", 0))

    opportunity_score = (
        move_score    * 0.35 +
        vol_score_opp * 0.25 +
        liq_score_opp * 0.20 +
        sqz_score_opp * 0.20
    )
    if not tradeable:
        opportunity_score = 0.0
    opportunity_score = round(min(100.0, opportunity_score), 1)

    log.debug(
        f"[CrashScore] score={crash_score:.1f} ({crash_probability}) "
        f"opp={opportunity_score:.1f} move={expected_move_pct:.1f}% ({move_classification}) "
        f"valid={signal_valid}"
    )

    return {
        "crash_score":         round(crash_score, 1),
        "crash_probability":   crash_probability,
        "urgency":             urgency,
        "recommended_action":  recommended_action,
        "signal_valid":        signal_valid,
        "component_scores":    component_scores,
        "weights":             WEIGHTS,
        "top_signals":         top_signals,
        "expected_move_pct":   expected_move_pct,
        "move_classification": move_classification,
        "tradeable":           tradeable,
        "opportunity_score":   opportunity_score,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc_funding_oi_score(leverage_result: dict) -> float:
    """
    Score de Funding+OI Divergence a partir do leverage_result.

    Combina:
      - funding_rate extremo
      - OI crescendo com preço caindo (divergência)
      - cascade_probability já captura ambos, mas esta sub-score
        foca especificamente na combinação funding+OI
    """
    funding_rate    = float(leverage_result.get("funding_rate", 0))
    oi_change       = float(leverage_result.get("oi_change_pct", 0))
    oi_divergence   = bool(leverage_result.get("oi_price_divergence", False))
    funding_extreme = bool(leverage_result.get("funding_extreme", False))
    cascade_prob    = float(leverage_result.get("cascade_probability", 0))

    score = 0.0

    # Base: cascade_probability já inclui funding + OI
    score += cascade_prob * 0.60

    # Bônus por divergência OI/preço (situação clássica de armadilha)
    if oi_divergence:
        score += 20.0

    # Bônus por funding extremo
    if funding_extreme:
        score += 20.0

    return min(100.0, score)


def _classify_score(score: float) -> str:
    """Classifica o crash score em LOW/MEDIUM/HIGH/EXTREME."""
    for label, threshold in SCORE_THRESHOLDS.items():
        if score >= threshold:
            return label
    return "LOW"


def _classify_expected_move(pct: float) -> str:
    """Classifica o expected move em MICRO/WEAK/TRADEABLE/STRONG/EXTREME."""
    if pct >= 25: return "EXTREME"
    if pct >= 18: return "STRONG"
    if pct >= 12: return "TRADEABLE"
    if pct >=  8: return "WEAK"
    return "MICRO"


def _extract_top_signals(
    liq: dict, lev: dict, whl: dict, comp: dict, cas: dict
) -> list:
    """Extrai lista de strings descrevendo os principais sinais ativos."""
    signals = []

    # Liquidity
    if liq.get("thin_book"):
        signals.append("Orderbook fino — liquidez desaparecendo")
    if liq.get("ask_wall_detected"):
        signals.append(f"Muro de venda detectado (imbalance {liq.get('bid_ask_imbalance', 0):.2f})")
    if liq.get("bid_support_collapse"):
        signals.append("Colapso do suporte de compra")

    # Leverage
    if lev.get("oi_spike"):
        signals.append(f"OI spike +{lev.get('oi_change_pct', 0):.1f}% (alavancagem acumulando)")
    if lev.get("funding_extreme"):
        ann = lev.get("funding_annualized", 0)
        signals.append(f"Funding extremo ({ann:.0f}% a.a.) — longs sobrecarregados")
    if lev.get("oi_price_divergence"):
        signals.append("OI subindo + preço estagnado = armadilha de longs")
    if lev.get("long_heavy"):
        signals.append(f"Long/Short ratio {lev.get('long_short_ratio', 0):.1f}x — mercado sobrecomprado")

    # Whale
    if whl.get("large_sell_detected"):
        dump = whl.get("estimated_dump_usd", 0)
        signals.append(f"Whale dump: ${dump/1e6:.1f}M em ordens de venda")
    if whl.get("cvd_negative"):
        signals.append(f"CVD negativo ({whl.get('cvd_score', 0):.2f}) — pressão vendedora oculta")

    # Compression
    if comp.get("compression_detected"):
        dur = comp.get("compression_duration_candles", 0)
        signals.append(f"Compressão de volatilidade ({dur} velas) — breakout iminente")
    if comp.get("expected_direction") == "DOWN":
        signals.append("Lower highs detectados → breakout esperado para baixo")

    # Cascade
    if cas.get("cascade_strength", 0) >= 60:
        dd = cas.get("estimated_drawdown", 0)
        signals.append(f"Cascata projetada: -{dd:.1f}% se stops ativados")

    return signals[:6]  # máx 6 sinais
