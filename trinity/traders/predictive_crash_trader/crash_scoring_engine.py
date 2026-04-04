"""
crash_scoring_engine.py — Motor de Score de Crash (Cap. 19)

Combina todos os detectores com pesos institucionais:

  Liquidity Collapse     → peso 0.25  (colapso de orderbook = sinal mais direto)
  Leverage Pressure      → peso 0.20  (OI spike + funding extremo)
  Whale Dump             → peso 0.20  (pressão institucional de venda)
  Volatility Compression → peso 0.15  (breakout iminente de compressão)
  Funding + OI Div.      → peso 0.20  (funding alto + OI subindo = armadilha)

Nota: Funding+OI Divergence usa os campos do leverage_pressure_detector.
      O peso de 0.20 é aplicado separadamente sobre oi_price_divergence + funding_extreme.

crash_score [0-100]:
  0  - 40  → LOW    (mercado saudável, sem sinais)
  40 - 60  → MEDIUM (sinais emergentes, atenção)
  60 - 80  → HIGH   (múltiplos sinais alinhados, precaução)
  80 - 100 → EXTREME (crash iminente, ação imediata)

Saída:
  {
    "crash_score":        0-100,
    "crash_probability":  "LOW" | "MEDIUM" | "HIGH" | "EXTREME",
    "urgency":            "WATCH" | "ALERT" | "DANGER" | "CRITICAL",
    "recommended_action": str,
    "signal_valid":       bool,
    "component_scores": {
      "liquidity":     float,
      "leverage":      float,
      "whale":         float,
      "compression":   float,
      "funding_oi":    float,
      "cascade":       float,
    },
    "weights": dict,
    "top_signals": list[str],  # principais sinais detectados
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

    log.debug(
        f"[CrashScore] score={crash_score:.1f} ({crash_probability}) "
        f"valid={signal_valid} components_>50={components_above_50} "
        f"top={top_signals[:2]}"
    )

    return {
        "crash_score":        round(crash_score, 1),
        "crash_probability":  crash_probability,
        "urgency":            urgency,
        "recommended_action": recommended_action,
        "signal_valid":       signal_valid,
        "component_scores":   component_scores,
        "weights":            WEIGHTS,
        "top_signals":        top_signals,
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
