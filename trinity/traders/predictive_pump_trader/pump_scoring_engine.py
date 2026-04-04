"""
pump_scoring_engine.py — Motor de Score de Pump (Cap. 20)

Combina todos os detectores com pesos institucionais:

  Whale Accumulation     → peso 0.25  (acumulação institucional = sinal mais direto)
  Short Squeeze          → peso 0.20  (shorts presos = combustível para pump)
  Liquidity Gravity      → peso 0.20  (clusters de liquidez atraindo preço para cima)
  Breakout Pressure      → peso 0.15  (compressão + volume acumulando)
  Smart Money Accum.     → peso 0.20  (higher lows + absorption + CVD)

pump_score [0-100]:
  0  - 40  → LOW    (sem sinais de pump)
  40 - 60  → MEDIUM (sinais emergentes)
  60 - 80  → HIGH   (setup de pump se formando)
  80 - 100 → EXTREME (pump iminente)

Saída:
  {
    "pump_score":         0-100,
    "pump_probability":   "LOW" | "MEDIUM" | "HIGH" | "EXTREME",
    "urgency":            "WATCH" | "ALERT" | "READY" | "LAUNCH",
    "recommended_action": str,
    "signal_valid":       bool,
    "component_scores": {
      "whale":       float,
      "squeeze":     float,
      "gravity":     float,
      "breakout":    float,
      "smart_money": float,
    },
    "weights":        dict,
    "top_signals":    list[str],
    "pump_target":    float,   # preço alvo estimado (gravidade de liquidez)
  }
"""
import logging
from typing import Dict

log = logging.getLogger(__name__)

# ── Pesos do modelo ────────────────────────────────────────────────────────────
WEIGHTS = {
    "whale":       0.25,
    "squeeze":     0.20,
    "gravity":     0.20,
    "breakout":    0.15,
    "smart_money": 0.20,
}

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

MIN_SCORE_VALID  = 50.0
MIN_COMPONENTS   = 2


def score_pump(
    whale_result:     dict,
    squeeze_result:   dict,
    gravity_result:   dict,
    breakout_result:  dict,
    smartmoney_result: dict,
) -> dict:
    """
    Combina todos os detectores em um score de pump unificado.
    """
    # ── Extrai scores individuais ─────────────────────────────────────────
    whale_score   = float(whale_result.get("accumulation_strength", 0))
    squeeze_score = float(squeeze_result.get("squeeze_probability", 0))
    gravity_score = float(gravity_result.get("strength", 0))
    breakout_score = float(breakout_result.get("breakout_probability", 0))
    sm_score      = float(smartmoney_result.get("smart_money_confidence", 0))

    # ── Penalidades por direção errada ────────────────────────────────────
    # Se breakout direction = DOWN, penaliza breakout
    if breakout_result.get("breakout_direction") == "DOWN":
        breakout_score *= 0.3

    # Se gravity = DOWN, penaliza gravity
    if gravity_result.get("liquidity_gravity") == "DOWN":
        gravity_score *= 0.3

    # ── Score ponderado ───────────────────────────────────────────────────
    weighted = (
        whale_score    * WEIGHTS["whale"]       +
        squeeze_score  * WEIGHTS["squeeze"]     +
        gravity_score  * WEIGHTS["gravity"]     +
        breakout_score * WEIGHTS["breakout"]    +
        sm_score       * WEIGHTS["smart_money"]
    )

    # Bônus de convergência: se >= 3 componentes acima de 60 = sinais alinhados
    above_60 = sum(1 for s in [whale_score, squeeze_score, gravity_score, breakout_score, sm_score] if s >= 60)
    if above_60 >= 3:
        weighted = min(100.0, weighted * 1.10)

    pump_score = min(100.0, weighted)

    # ── Classificação ─────────────────────────────────────────────────────
    pump_probability  = _classify_score(pump_score)
    urgency           = URGENCY_MAP[pump_probability]
    recommended_action = ACTIONS[urgency]

    # ── Validação do sinal ────────────────────────────────────────────────
    component_scores = {
        "whale":       round(whale_score, 1),
        "squeeze":     round(squeeze_score, 1),
        "gravity":     round(gravity_score, 1),
        "breakout":    round(breakout_score, 1),
        "smart_money": round(sm_score, 1),
    }

    components_above_50 = sum(1 for v in component_scores.values() if v >= 50)
    signal_valid = pump_score >= MIN_SCORE_VALID and components_above_50 >= MIN_COMPONENTS

    # ── Preço alvo ────────────────────────────────────────────────────────
    pump_target = float(gravity_result.get("target_price", 0))

    # ── Top sinais detectados ─────────────────────────────────────────────
    top_signals = _extract_top_signals(
        whale_result, squeeze_result, gravity_result,
        breakout_result, smartmoney_result,
    )

    log.debug(
        f"[PumpScore] score={pump_score:.1f} ({pump_probability}) "
        f"valid={signal_valid} comp_>50={components_above_50} "
        f"top={top_signals[:2]}"
    )

    return {
        "pump_score":         round(pump_score, 1),
        "pump_probability":   pump_probability,
        "urgency":            urgency,
        "recommended_action": recommended_action,
        "signal_valid":       signal_valid,
        "component_scores":   component_scores,
        "weights":            WEIGHTS,
        "top_signals":        top_signals,
        "pump_target":        pump_target,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _classify_score(score: float) -> str:
    for label, threshold in SCORE_THRESHOLDS.items():
        if score >= threshold:
            return label
    return "LOW"


def _extract_top_signals(
    whale: dict, squeeze: dict, gravity: dict, breakout: dict, sm: dict,
) -> list:
    signals = []

    # Whale accumulation
    if whale.get("large_buy_detected"):
        acc = whale.get("estimated_accumulation_usd", 0)
        signals.append(f"Whale acumulando: ${acc/1e6:.1f}M em ordens de compra")
    if whale.get("cvd_positive"):
        cvd = whale.get("cvd_score", 0)
        signals.append(f"CVD positivo ({cvd:.2f}) — compra institucional oculta")
    if whale.get("buy_pressure_dominant"):
        ratio = whale.get("buy_pressure_ratio", 0)
        signals.append(f"Pressão compradora: {ratio*100:.0f}% do volume = longs dominando")

    # Short squeeze
    if squeeze.get("shorts_trapped"):
        ls = squeeze.get("long_short_ratio", 0)
        fund = squeeze.get("funding_annualized", 0)
        signals.append(f"Shorts presos: L/S {ls:.2f}x | Funding {fund:.0f}% a.a.")
    elif squeeze.get("short_heavy"):
        signals.append(f"Mercado short-heavy (L/S {squeeze.get('long_short_ratio', 0):.2f}x) — squeeze fuel")

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

    # Smart money
    if sm.get("absorption_detected"):
        signals.append("Absorption detectada — mão forte absorvendo vendas")
    if sm.get("hidden_buying"):
        signals.append(f"Hidden buying: CVD {sm.get('cvd_score', 0):.3f} com preço comprimido")
    if sm.get("higher_lows_pattern"):
        cnt = sm.get("higher_lows_count", 0)
        signals.append(f"Higher lows ({cnt} consecutivos) — estrutura bullish formando")

    return signals[:6]
