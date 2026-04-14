"""
crash_scoring_engine.py — Motor de Score de Crash (Cap. 19) — v4 4×25 Gate.io

Arquitetura 4×25 = 100 pts máximo:
  cascade_m4  (0-25) → LiquidationCascadeDetector  — combustível de cascata M4
  collapse    (0-25) → LiquidityCollapseDetector    — colapso de livro de ordens
  whale       (0-25) → WhaleDumpDetector            — distribuição institucional
  volatility  (0-25) → VolatilityCompressionDetector — breakout de compressão

opportunity_score = sum(4 × 25) → 0-100
  Penalidade 0.5× se expected_move < 6% (sem zerar)
  Bônus DNA 1.20× se padrão institucional detectado

lev_result  disponível via coin_data["_leverage_result"] para DNA e expected_move
cas_legacy  disponível via coin_data["_cascade_result"]  para expected_move

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DNA CRASH PATTERNS (50+ casos históricos):
  PATTERN_A — Over-Leveraged Cascade  (85% acurácia)
  PATTERN_B — Whale Distribution      (72% acurácia)
  PATTERN_C — Stop Hunt Spiral        (68% acurácia)
  PATTERN_D — Bid Support Collapse    (63% acurácia)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

crash_score [0-100]:
  0  - 40  → LOW    (mercado saudável)
  40 - 60  → MEDIUM (sinais emergentes)
  60 - 80  → HIGH   (múltiplos sinais alinhados)
  80 - 100 → EXTREME (crash iminente)
"""
import logging
from typing import Tuple

from trinity.traders.btc_regime_monitor import get_btc_regime

log = logging.getLogger(__name__)

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

MIN_SCORE_VALID  = 30.0
MIN_COMPONENTS   = 1      # mínimo de 1 componente >= 10/25


def score_crash(
    cascade_result:   dict,
    liquidity_result: dict,
    whale_result:     dict,
    vol_result:       dict,
    coin_data:        dict = None,
) -> dict:
    """
    Combina detectores com arquitetura 4×25 (total 0-100).

    Args:
        cascade_result:   output de detect_liquidation_cascade() — M4 (0-25)
        liquidity_result: output de detect_liquidity_collapse()  — severity (0-100)
        whale_result:     output de detect_whale_dump()          — risk (0-100)
        vol_result:       output de detect_volatility_compression() — breakout_probability (0-100)
        coin_data:        dict completo do coin (contém "_leverage_result" e "_cascade_result")
    """
    _cd = coin_data or {}

    # Resultados injetados pelo orquestrador
    lev_result = _cd.get("_leverage_result", {})
    cas_legacy = _cd.get("_cascade_result",  {})

    # ── 4×25 component scores ─────────────────────────────────────────────
    cascade_score  = min(25.0, float(cascade_result.get("score", 0)))
    collapse_score = min(25.0, float(liquidity_result.get("severity", 0)) * 0.25)
    whale_score    = min(25.0, float(whale_result.get("risk", 0)) * 0.25)
    vol_score      = min(25.0, float(vol_result.get("breakout_probability", 0)) * 0.25)

    # ── Opportunity score base (soma dos 4 componentes) ───────────────────
    opportunity_score_raw = cascade_score + collapse_score + whale_score + vol_score

    # ── DNA Pattern Matching ──────────────────────────────────────────────
    dna_bonus, dna_pattern = _detect_crash_dna(
        liquidity_result, lev_result, whale_result, cas_legacy
    )

    # crash_score = 4×25 + DNA bonus (para classificação interna)
    crash_score_val = min(100.0, opportunity_score_raw + dna_bonus)

    # ── Classificação ─────────────────────────────────────────────────────
    crash_probability  = _classify_score(crash_score_val)
    urgency            = URGENCY_MAP[crash_probability]
    recommended_action = ACTIONS[urgency]

    # ── Validação do sinal ────────────────────────────────────────────────
    component_scores = {
        "cascade":   round(cascade_score, 1),
        "collapse":  round(collapse_score, 1),
        "whale":     round(whale_score, 1),
        "volatility": round(vol_score, 1),
    }

    components_above_threshold = sum(1 for v in component_scores.values() if v >= 10.0)
    signal_valid = crash_score_val >= MIN_SCORE_VALID and components_above_threshold >= MIN_COMPONENTS

    # ── Top sinais detectados ─────────────────────────────────────────────
    top_signals = _extract_top_signals(
        liquidity_result, lev_result, whale_result, vol_result, cas_legacy
    )
    if dna_pattern:
        top_signals.insert(0, f"DNA: {dna_pattern}")
    top_signals = top_signals[:6]

    # ── Expected Move Model ────────────────────────────────────────────────
    price    = float(_cd.get("price", 0))
    high_24h = float(_cd.get("high_24h", price * 1.05))
    low_24h  = float(_cd.get("low_24h",  price * 0.95))
    ls_ratio = float(_cd.get("long_short_ratio", 1.0))

    daily_range_pct = ((high_24h - low_24h) / price * 100) if price > 0 else 10.0

    # Base: cascade estimated_drawdown
    base_dd = float(cas_legacy.get("estimated_drawdown", 0))

    # Multiplicador de volatilidade
    if   daily_range_pct > 20: vol_mult = 1.6
    elif daily_range_pct > 12: vol_mult = 1.3
    elif daily_range_pct >  7: vol_mult = 1.1
    else:                      vol_mult = 1.0

    # Amplificador por longs alavancados
    lev_amp = 1.0 + max(0.0, (ls_ratio - 1.5) * 0.25)

    # squeeze_victim: L/S alto = liquidações mais violentas
    squeeze_victim = 1.5 if ls_ratio > 2.0 else (1.2 if ls_ratio > 1.7 else 1.0)

    # cascade_acceleration: cascata forte auto-amplifica
    cascade_str_val = float(cas_legacy.get("cascade_strength", 0))
    cascade_acc = 1.3 if cascade_str_val > 70 else (1.15 if cascade_str_val > 50 else 1.0)

    expected_move_pct = base_dd * vol_mult * lev_amp * squeeze_victim * cascade_acc

    # Floor: cascata fraca mas mercado volátil
    if base_dd < 4.0 and daily_range_pct > 8.0:
        expected_move_pct = max(expected_move_pct, daily_range_pct * 0.55)

    # ── Floor adicional baseado em score dos componentes ──────────────────
    if opportunity_score_raw >= 40 and expected_move_pct < 4.0:
        expected_move_pct = max(expected_move_pct, daily_range_pct * 0.40)
    if opportunity_score_raw >= 60 and expected_move_pct < 6.0:
        expected_move_pct = max(expected_move_pct, 6.0)

    expected_move_pct   = round(min(35.0, expected_move_pct), 1)
    move_classification = _classify_expected_move(expected_move_pct)
    tradeable           = expected_move_pct >= 4.0  # baixado de 6% para 4%

    # ── Overextension Multiplier ───────────────────────────────────────────
    # Coins que já subiram muito têm maior probabilidade de crash violento
    pct_change_24h = float(_cd.get("price_change_pct", 0))

    overext_mult = 1.0
    if   pct_change_24h > 150: overext_mult = 1.70
    elif pct_change_24h > 100: overext_mult = 1.50
    elif pct_change_24h > 70:  overext_mult = 1.35
    elif pct_change_24h > 50:  overext_mult = 1.25
    elif pct_change_24h > 30:  overext_mult = 1.15
    elif pct_change_24h > 20:  overext_mult = 1.08

    if overext_mult > 1.0:
        top_signals.insert(0, f"⚠️ OVEREXTENDED +{pct_change_24h:.0f}% 24h (boost ×{overext_mult:.2f})")

    # ── Opportunity Score final ────────────────────────────────────────────
    # ORDEM: overext_mult → penalidade → DNA → BTC boost → round
    opportunity_score = opportunity_score_raw * overext_mult

    # 1. Penalidade (×0.70) se expected_move < 4% — aplicar ANTES do DNA
    if not tradeable:
        opportunity_score *= 0.70

    # 2. Bônus DNA: eleva o score se padrão institucional confirmado — DEPOIS
    if dna_pattern:
        opportunity_score = min(100.0, opportunity_score * 1.25)

    # ── BTC Regime Correlation Boost ───────────────────────────────────────
    try:
        btc = get_btc_regime()
        btc_bias       = btc.get("bias", "NEUTRAL")
        btc_strength   = btc.get("strength", 0)
        btc_transition = btc.get("transition", "")
        t_boost        = btc.get("transition_boost", 1.0)
        confirmations  = btc.get("confirmations", 0)

        if btc_bias == "CRASH":
            # BTC bearish + alt crash = confluência de mercado
            if btc_transition and t_boost > 1.0:
                opportunity_score = min(100.0, opportunity_score * t_boost)
                top_signals.append(f"🔄 {btc_transition}")
            elif btc_strength >= 50 and confirmations >= 3:
                opportunity_score = min(100.0, opportunity_score * 1.25)
            elif btc_strength >= 30 and confirmations >= 2:
                opportunity_score = min(100.0, opportunity_score * 1.15)
            elif btc_strength >= 15:
                opportunity_score = min(100.0, opportunity_score * 1.08)

        elif btc_bias == "PUMP":
            # BTC bullish contradiz crash — penalizar
            if btc_strength >= 50 and confirmations >= 3:
                opportunity_score *= 0.70
            elif btc_strength >= 30:
                opportunity_score *= 0.80
            elif btc_strength >= 15:
                opportunity_score *= 0.90

        # Sinal macro NFCI no Telegram
        nfci_regime = btc.get("nfci_regime", "NEUTRAL")
        if nfci_regime == "RISK_ON":
            top_signals.append(f"🏛️ NFCI RISK ON ({btc.get('nfci_value', 0):.3f}) — macro favorável")
        elif nfci_regime == "RISK_OFF":
            top_signals.append(f"🏛️ NFCI RISK OFF ({btc.get('nfci_value', 0):.3f}) — macro desfavorável")

        # Sinais BTC no Telegram
        if btc.get("signals") and btc_strength >= 20:
            for sig in btc["signals"][:2]:
                top_signals.append(f"📊 {sig}")
            top_signals = top_signals[:8]

    except Exception as e:
        log.debug(f"[CrashScore] BTC regime error: {e}")

    opportunity_score = round(min(100.0, opportunity_score), 1)

    log.debug(
        f"[CrashScore] score={crash_score_val:.1f} ({crash_probability}) "
        f"dna='{dna_pattern}' dna_bonus={dna_bonus:.1f} "
        f"opp={opportunity_score:.1f} move={expected_move_pct:.1f}% ({move_classification}) "
        f"casc={cascade_score:.1f} coll={collapse_score:.1f} "
        f"whale={whale_score:.1f} vol={vol_score:.1f}"
    )

    return {
        "crash_score":         round(crash_score_val, 1),
        "crash_probability":   crash_probability,
        "urgency":             urgency,
        "recommended_action":  recommended_action,
        "signal_valid":        signal_valid,
        "component_scores":    component_scores,
        "top_signals":         top_signals,
        "dna_pattern":         dna_pattern,
        "expected_move_pct":   expected_move_pct,
        "move_classification": move_classification,
        "tradeable":           tradeable,
        "opportunity_score":   opportunity_score,
    }


# ── DNA Pattern Matching ───────────────────────────────────────────────────────

def _detect_crash_dna(
    liq: dict, lev: dict, whl: dict, cas: dict
) -> Tuple[float, str]:
    """
    Detecta padrões DNA de crash calibrados em 50+ casos históricos.

    lev = coin_data["_leverage_result"] (leverage_pressure_detector)
    cas = coin_data["_cascade_result"]  (cascade_prediction_model)
    """
    patterns = []

    # PATTERN_A: Over-Leveraged Cascade (85% acurácia)
    if (lev.get("funding_extreme") and
            lev.get("long_heavy") and
            lev.get("oi_spike") and
            liq.get("thin_book")):
        patterns.append((18.0, "OVER-LEVERAGED CASCADE"))

    # PATTERN_B: Whale Distribution (72% acurácia)
    if (whl.get("large_sell_detected") and
            whl.get("cvd_negative") and
            lev.get("funding_extreme")):
        patterns.append((14.0, "WHALE DISTRIBUTION"))

    # PATTERN_C: Stop Hunt Spiral (68% acurácia)
    if (liq.get("thin_book") and
            lev.get("oi_price_divergence") and
            float(cas.get("cascade_strength", 0)) >= 50):
        patterns.append((12.0, "STOP HUNT SPIRAL"))

    # PATTERN_D: Bid Support Collapse (63% acurácia)
    if (liq.get("bid_support_collapse") and
            whl.get("large_sell_detected")):
        patterns.append((10.0, "BID SUPPORT COLLAPSE"))

    if not patterns:
        return 0.0, ""

    patterns.sort(key=lambda x: x[0], reverse=True)
    top_bonus, top_pattern = patterns[0]

    # Confluência DNA: 2+ padrões = confirmação cruzada
    if len(patterns) >= 2:
        secondary_bonus = patterns[1][0] * 0.30
        top_bonus = min(25.0, top_bonus + secondary_bonus)
        top_pattern = f"{top_pattern} + {patterns[1][1]}"

    return top_bonus, top_pattern


# ── Helpers ───────────────────────────────────────────────────────────────────

def _classify_score(score: float) -> str:
    for label, threshold in SCORE_THRESHOLDS.items():
        if score >= threshold:
            return label
    return "LOW"


def _classify_expected_move(pct: float) -> str:
    if pct >= 25: return "EXTREME"
    if pct >= 18: return "STRONG"
    if pct >= 12: return "TRADEABLE"
    if pct >=  6: return "WEAK"
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

    # Leverage (via _leverage_result)
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

    # Volatility
    if comp.get("compression_detected"):
        dur = comp.get("compression_duration_candles", 0)
        signals.append(f"Compressão de volatilidade ({dur} velas) — breakout iminente")
    if comp.get("expected_direction") == "DOWN":
        signals.append("Lower highs detectados → breakout esperado para baixo")

    # Cascade (legacy)
    if float(cas.get("cascade_strength", 0)) >= 60:
        dd = cas.get("estimated_drawdown", 0)
        signals.append(f"Cascata projetada: -{dd:.1f}% se stops ativados")

    return signals[:6]
