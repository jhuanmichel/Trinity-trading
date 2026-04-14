"""
pump_scoring_engine.py — Motor de Score de Pump (Cap. 20) — v4 4×25 Gate.io

Arquitetura 4×25 = 100 pts máximo:
  silent_acc  (0-25) → SilentAccumulationDetector  — acumulação institucional quieta
  squeeze     (0-25) → ShortSqueezeDetector         — shorts presos, funding negativo
  gravity     (0-25) → LiquidityGravityDetector     — ímã de liquidez UP
  breakout    (0-25) → BreakoutPressureDetector      — compressão + iminência de breakout

opportunity_score = sum(4 × 25) → 0-100
  Penalidade 0.5× se expected_move < 6% (sem zerar)
  Bônus DNA 1.20× se padrão institucional detectado

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DNA PUMP PATTERNS (50+ casos históricos):
  PATTERN_A — Short Squeeze Ignition  (78% acurácia)
  PATTERN_B — Compression Launch      (70% acurácia)
  PATTERN_C — Hidden Accumulation     (65% acurácia)
  PATTERN_D — Liquidity Hunt Up       (72% acurácia)
  PATTERN_E — Silent OI Breakout      (60% acurácia)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

pump_score [0-100]:
  0  - 40  → LOW    (sem sinais de pump)
  40 - 60  → MEDIUM (sinais emergentes)
  60 - 80  → HIGH   (setup de pump se formando)
  80 - 100 → EXTREME (pump iminente)
"""
import logging
from typing import Tuple

from trinity.traders.btc_regime_monitor import get_btc_regime
from trinity.traders.funding_extreme_engine import analyze_funding_extreme

log = logging.getLogger(__name__)

# ── Limiares de classificação ──────────────────────────────────────────────────
SCORE_THRESHOLDS = {
    "EXTREME": 80,
    "HIGH":    60,
    "MEDIUM":  40,
    "LOW":     0,
}

URGENCY_MAP = {
    "EXTREME": "LAUNCH",
    "HIGH":    "READY",
    "MEDIUM":  "ALERT",
    "LOW":     "WATCH",
}

ACTIONS = {
    "LAUNCH": "LONG imediato — pump iniciando, entrar agora ou perder",
    "READY":  "Setup completo — preparar long com confirmação de volume",
    "ALERT":  "Sinal emergente — monitorar, aguardar confirmação",
    "WATCH":  "Acumulação silenciosa — observar próximas 15min",
}

MIN_SCORE_VALID  = 30.0
MIN_COMPONENTS   = 1      # mínimo de 1 componente >= 10/25


def score_pump(
    silent_result:   dict,
    squeeze_result:  dict,
    gravity_result:  dict,
    breakout_result: dict,
    coin_data:       dict = None,
) -> dict:
    """
    Combina detectores com arquitetura 4×25 (total 0-100).

    Args:
        silent_result:   output de detect_silent_accumulation()
        squeeze_result:  output de detect_short_squeeze()
        gravity_result:  output de detect_liquidity_gravity()
        breakout_result: output de detect_breakout_pressure()
        coin_data:       dict completo do coin para expected_move
    """
    # ── 4×25 component scores ─────────────────────────────────────────────
    silent_score   = min(25.0, float(silent_result.get("score", 0)))
    squeeze_score  = min(25.0, float(squeeze_result.get("squeeze_probability", 0)) * 0.25)
    gravity_score  = min(25.0, float(gravity_result.get("strength", 0)) * 0.25)
    breakout_score = min(25.0, float(breakout_result.get("breakout_probability", 0)) * 0.25)

    # ── Penalidades por direção errada ────────────────────────────────────
    if breakout_result.get("breakout_direction") == "DOWN":
        breakout_score *= 0.3
    if gravity_result.get("liquidity_gravity") == "DOWN":
        gravity_score *= 0.3

    # ── Opportunity score base (soma dos 4 componentes) ───────────────────
    opportunity_score_raw = silent_score + squeeze_score + gravity_score + breakout_score

    # ── DNA Pattern Matching ──────────────────────────────────────────────
    dna_bonus, dna_pattern = _detect_pump_dna(
        silent_result, squeeze_result, gravity_result, breakout_result
    )

    # pump_score = 4×25 + DNA bonus (para classificação interna)
    pump_score = min(100.0, opportunity_score_raw + dna_bonus)

    # ── Classificação ─────────────────────────────────────────────────────
    pump_probability   = _classify_score(pump_score)
    urgency            = URGENCY_MAP[pump_probability]
    recommended_action = ACTIONS[urgency]

    # ── Validação do sinal ────────────────────────────────────────────────
    component_scores = {
        "silent_acc": round(silent_score, 1),
        "squeeze":    round(squeeze_score, 1),
        "gravity":    round(gravity_score, 1),
        "breakout":   round(breakout_score, 1),
    }

    components_above_threshold = sum(1 for v in component_scores.values() if v >= 10.0)
    signal_valid = pump_score >= MIN_SCORE_VALID and components_above_threshold >= MIN_COMPONENTS

    # ── Preço alvo ────────────────────────────────────────────────────────
    pump_target = float(gravity_result.get("target_price", 0))

    # ── Top sinais detectados ─────────────────────────────────────────────
    top_signals = _extract_top_signals(
        silent_result, squeeze_result, gravity_result, breakout_result
    )
    if dna_pattern:
        top_signals.insert(0, f"DNA: {dna_pattern}")
    top_signals = top_signals[:6]

    # ── Expected Move Model ────────────────────────────────────────────────
    _cd          = coin_data or {}
    price        = float(_cd.get("price", 0))
    high_24h     = float(_cd.get("high_24h", price * 1.05))
    low_24h      = float(_cd.get("low_24h",  price * 0.95))
    ls_ratio     = float(_cd.get("long_short_ratio", 1.0))
    funding_rate = float(_cd.get("funding_rate", 0))

    daily_range_pct = ((high_24h - low_24h) / price * 100) if price > 0 else 10.0

    # Base: distância ao target de liquidez (ímã de shorts)
    if pump_target > price > 0:
        base_move = (pump_target - price) / price * 100
    else:
        base_move = 0.0

    # Multiplicador de short squeeze calibrado por casos históricos
    if   ls_ratio < 0.55: squeeze_mult = 3.8
    elif ls_ratio < 0.65: squeeze_mult = 2.8
    elif ls_ratio < 0.75: squeeze_mult = 2.2
    elif ls_ratio < 0.85: squeeze_mult = 1.7
    elif ls_ratio < 0.95: squeeze_mult = 1.2
    else:                 squeeze_mult = 1.0

    # Funding negativo = pressão extra no squeeze
    fund_ann = funding_rate * 3.0 * 365.0 * 100.0  # % ao ano
    if   fund_ann < -150.0: squeeze_mult *= 1.40
    elif fund_ann < -100.0: squeeze_mult *= 1.30
    elif fund_ann <  -50.0: squeeze_mult *= 1.15

    # OI fuel factor — OI crescendo com shorts dominando = combustível extra
    oi_growing = squeeze_result.get("oi_growing", False)
    if oi_growing and ls_ratio < 0.85:
        squeeze_mult *= 1.20

    # Floor: shorts pesados + mercado volátil = mínimo garantido
    if ls_ratio < 0.85 and daily_range_pct > 8.0:
        base_move = max(base_move, daily_range_pct * 0.60)

    # ── Floor baseado em score dos componentes ─────────────────────────────
    # Garante que sinais fortes não gerem expected_move irrisório
    component_avg = opportunity_score_raw / 4.0
    if component_avg >= 15.0 and base_move < daily_range_pct * 0.70:
        base_move = max(base_move, daily_range_pct * 0.70)
    elif component_avg >= 10.0 and base_move < daily_range_pct * 0.50:
        base_move = max(base_move, daily_range_pct * 0.50)
    elif component_avg >= 6.0 and base_move < daily_range_pct * 0.30:
        base_move = max(base_move, daily_range_pct * 0.30)

    # Floor absoluto: score >= 40 e move < 4% = forçar mínimo
    if opportunity_score_raw >= 40 and base_move < 4.0:
        base_move = 4.0

    expected_move_pct   = round(min(40.0, base_move * squeeze_mult), 1)
    move_classification = _classify_expected_move(expected_move_pct)
    tradeable           = expected_move_pct >= 4.0  # baixado de 6% para 4%

    # ── Momentum Acceleration Multiplier ──────────────────────────────────
    # Pump em andamento (+8% a +50%) é sinal de força — ainda pode continuar
    pct_change_pump = float(_cd.get("price_change_pct", 0))
    fund_ann_mom    = funding_rate * 3.0 * 365.0 * 100.0

    momentum_mult = 1.0
    if 8.0 <= pct_change_pump < 25.0:
        # Funding negativo + pump = short squeeze ativo → boost máximo
        momentum_mult = 1.35 if fund_ann_mom < -30.0 else 1.20
    elif 25.0 <= pct_change_pump <= 50.0:
        momentum_mult = 1.10

    if momentum_mult >= 1.35:
        top_signals.insert(0,
            f"🚀 SQUEEZE ATIVO +{pct_change_pump:.0f}% | funding {fund_ann_mom:.0f}%/yr (boost ×{momentum_mult:.2f})")
    elif momentum_mult >= 1.20:
        top_signals.insert(0,
            f"📈 MOMENTUM +{pct_change_pump:.0f}% 24h (boost ×{momentum_mult:.2f})")
    elif momentum_mult > 1.0:
        top_signals.insert(0,
            f"⚡ CONTINUAÇÃO +{pct_change_pump:.0f}% 24h (boost ×{momentum_mult:.2f})")

    # ── Funding Extreme Analysis (motor 4D) ────────────────────────────────
    funding_analysis    = analyze_funding_extreme(coin_data or {}, direction="PUMP")
    funding_extreme_mult = funding_analysis["composite_mult"]

    # Sinais de funding vão ao TOPO — são os mais importantes
    if funding_analysis["tier"] != "NORMAL":
        for sig in reversed(funding_analysis["signals"][:3]):
            top_signals.insert(0, sig)

    # ── Opportunity Score final ────────────────────────────────────────────
    # ORDEM: funding_extreme → momentum → penalidade → DNA → BTC boost → round
    opportunity_score = opportunity_score_raw

    # Funding extreme — o sinal mais forte em cripto futures (PRIMEIRO)
    if funding_extreme_mult > 1.0:
        opportunity_score = min(100.0, opportunity_score * funding_extreme_mult)

    # Momentum acceleration — pump ativo
    if momentum_mult > 1.0:
        opportunity_score = min(100.0, opportunity_score * momentum_mult)

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

        if btc_bias == "PUMP":
            # BTC bullish + alt pump = confluência de mercado
            if btc_transition and t_boost > 1.0:
                # TRANSIÇÃO ativa — sinal de ouro, boost máximo
                opportunity_score = min(100.0, opportunity_score * t_boost)
                top_signals.append(f"🔄 {btc_transition}")
            elif btc_strength >= 50 and confirmations >= 3:
                opportunity_score = min(100.0, opportunity_score * 1.25)
            elif btc_strength >= 30 and confirmations >= 2:
                opportunity_score = min(100.0, opportunity_score * 1.15)
            elif btc_strength >= 15:
                opportunity_score = min(100.0, opportunity_score * 1.08)

        elif btc_bias == "CRASH":
            # BTC bearish contradiz pump — penalizar proporcional
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
        log.debug(f"[PumpScore] BTC regime error: {e}")

    opportunity_score = round(min(100.0, opportunity_score), 1)

    log.debug(
        f"[PumpScore] score={pump_score:.1f} ({pump_probability}) "
        f"dna='{dna_pattern}' dna_bonus={dna_bonus:.1f} "
        f"opp={opportunity_score:.1f} move={expected_move_pct:.1f}% ({move_classification}) "
        f"silent={silent_score:.1f} sqz={squeeze_score:.1f} "
        f"grav={gravity_score:.1f} brk={breakout_score:.1f}"
    )

    return {
        "pump_score":          round(pump_score, 1),
        "pump_probability":    pump_probability,
        "urgency":             urgency,
        "recommended_action":  recommended_action,
        "signal_valid":        signal_valid,
        "component_scores":    component_scores,
        "top_signals":         top_signals,
        "pump_target":         pump_target,
        "dna_pattern":         dna_pattern,
        "expected_move_pct":   expected_move_pct,
        "move_classification": move_classification,
        "tradeable":           tradeable,
        "opportunity_score":   opportunity_score,
    }


# ── DNA Pattern Matching ───────────────────────────────────────────────────────

def _detect_pump_dna(
    silent: dict, squeeze: dict, gravity: dict, breakout: dict
) -> Tuple[float, str]:
    """
    Detecta padrões DNA de pump (50+ casos históricos de altcoins).

    Retorna (bonus_pts, pattern_name) ou (0.0, "").
    """
    patterns = []

    # PATTERN_A: Short Squeeze Ignition (78% acurácia)
    # Shorts presos + gravidade UP + compra oculta (CVD positivo)
    if (squeeze.get("shorts_trapped") and
            gravity.get("liquidity_gravity") == "UP" and
            float(silent.get("cvd_value", 0)) > 0.15):
        patterns.append((20.0, "SHORT SQUEEZE IGNITION"))

    # PATTERN_B: Compression Launch (70% acurácia)
    # Compressão ativa + mercado short-heavy + acumulação silenciosa detectada
    if (breakout.get("compression_detected") and
            squeeze.get("short_heavy") and
            silent.get("silent_acc")):
        patterns.append((15.0, "COMPRESSION LAUNCH"))

    # PATTERN_C: Hidden Accumulation (65% acurácia)
    # Acumulação silenciosa confirmada + CVD positivo + volume crescendo
    if (silent.get("silent_acc") and
            float(silent.get("cvd_value", 0)) > 0.15 and
            breakout.get("volume_building")):
        patterns.append((12.0, "HIDDEN ACCUMULATION"))

    # PATTERN_D: Liquidity Hunt Up (72% acurácia)
    # Bid wall sólido + gravidade UP + shorts pesados como ímã
    if (gravity.get("bid_wall_support") and
            gravity.get("liquidity_gravity") == "UP" and
            squeeze.get("short_heavy")):
        patterns.append((13.0, "LIQUIDITY HUNT UP"))

    # PATTERN_E: Silent OI Breakout (60% acurácia)
    # OI crescendo quietamente (score > 5/8) + gravidade UP + volume crescendo
    if (float(silent.get("oi_score", 0)) >= 5.0 and
            gravity.get("liquidity_gravity") == "UP" and
            breakout.get("volume_building")):
        patterns.append((10.0, "SILENT OI BREAKOUT"))

    if not patterns:
        return 0.0, ""

    patterns.sort(key=lambda x: x[0], reverse=True)
    top_bonus, top_pattern = patterns[0]

    # Confluência DNA: 2+ padrões simultâneos = setup excepcional
    if len(patterns) >= 2:
        secondary_bonus = patterns[1][0] * 0.35
        top_bonus = min(28.0, top_bonus + secondary_bonus)
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
    silent: dict, squeeze: dict, gravity: dict, breakout: dict,
) -> list:
    signals = []

    # Silent accumulation
    if silent.get("silent_acc"):
        s = silent.get("score", 0)
        signals.append(f"Acumulação silenciosa detectada (score {s:.1f}/25)")
    if float(silent.get("cvd_value", 0)) > 0.15:
        cvd = silent.get("cvd_value", 0)
        signals.append(f"CVD positivo ({cvd:.3f}) — compra institucional oculta")
    if float(silent.get("funding_rate", 0)) < -0.0001:
        fr = silent.get("funding_rate", 0) * 3 * 365 * 100
        signals.append(f"Funding negativo ({fr:.0f}%/yr) — shorts pagando longs")
    if float(silent.get("oi_change_pct", 0)) > 3.0 and float(silent.get("oi_score", 0)) >= 4:
        oi = silent.get("oi_change_pct", 0)
        signals.append(f"OI crescendo +{oi:.1f}% sem pump = acumulação quieta")

    # Short squeeze
    if squeeze.get("shorts_trapped"):
        ls = squeeze.get("long_short_ratio", 0)
        fund = squeeze.get("funding_annualized", 0)
        signals.append(f"Shorts presos: L/S {ls:.2f}x | Funding {fund:.0f}% a.a.")
    elif squeeze.get("short_heavy"):
        signals.append(f"Mercado short-heavy (L/S {squeeze.get('long_short_ratio', 0):.2f}x) — squeeze fuel")
    if squeeze.get("oi_growing") and squeeze.get("funding_negative"):
        signals.append("OI crescendo + funding negativo = mais combustível de squeeze")

    # Liquidity gravity
    if gravity.get("liquidity_gravity") == "UP":
        target = gravity.get("target_price", 0)
        strength = gravity.get("strength", 0)
        if target:
            signals.append(f"Ímã de liquidez em ${target:.4f} (força: {strength:.0f})")
    if gravity.get("bid_wall_support"):
        signals.append("Bid wall forte — suporte sólido como rampa de lançamento")

    # Breakout
    if breakout.get("compression_detected"):
        dur = breakout.get("compression_duration_candles", 0)
        signals.append(f"Compressão ativa ({dur} velas) — breakout de alta iminente")
    if breakout.get("volume_building"):
        vr = breakout.get("volume_ratio", 0)
        signals.append(f"Volume crescendo {vr:.1f}x durante compressão — acumulação")

    return signals[:6]
