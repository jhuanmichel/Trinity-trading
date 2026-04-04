"""
rare_setup_detector.py — Detector de Setups Raros
Capítulo 3 — Trinity Trading v1.0

Um Setup Raro ocorre quando 3+ fatores técnicos INDEPENDENTES de alta
probabilidade convergem simultaneamente no mesmo nível de preço.

Ocorrência esperada: 5 a 15 vezes por mês por ativo.

Tipos principais:
  1. OB + FVG + Sweep          ← mais poderoso / recorrente
  2. Sweep + Liq + MTF         ← varredura com liquidações
  3. Trap + Reversão           ← armadilha detectada + reversão

Pesos:
  htf_bias:      20  — direção Diário/Semanal suporta o sinal
  order_block:   20  — OB válido na zona de entrada atual
  fvg:           15  — FVG sobreposto ou adjacente ao preço
  recent_sweep:  20  — varredura de pool de liquidez próxima
  liquidations:  15  — pico de liquidações na direção esperada
  volume:        10  — volume confirma a direção
  TOTAL:        100  — >= 75 → SETUP RARO ativado
"""

RSD_WEIGHTS = {
    "htf_bias":     20,
    "order_block":  20,
    "fvg":          15,
    "recent_sweep": 20,
    "liquidations": 15,
    "volume":       10,
}

RARE_THRESHOLD     = 75     # score mínimo para ativar SETUP RARO
MIN_FACTORS_ACTIVE = 3      # mínimo de fatores com score >= 50


# ─── Componentes individuais (0-100 por fator) ─────────────────────────────

def _htf_bias(smc_signal: dict, trend_data: dict, direction: str) -> float:
    """
    Tendência HTF (alinhamento MTF do SMC) suporta a direção do sinal.
    Fontes: smc_signal['alignment'] + trend_data['bias']
    """
    if not direction or direction == "AGUARDANDO":
        return 0.0

    # Prioridade: alinhamento MTF do SMC engine
    if smc_signal:
        alignment = smc_signal.get("alignment", "MIXED").upper()
        if direction == "LONG":
            if "STRONG_BULLISH" in alignment: return 100.0
            if "BULLISH" in alignment:         return 75.0
            if "MIXED" in alignment:           return 35.0
            return 0.0
        elif direction == "SHORT":
            if "STRONG_BEARISH" in alignment: return 100.0
            if "BEARISH" in alignment:         return 75.0
            if "MIXED" in alignment:           return 35.0
            return 0.0

    # Fallback: trend indicator
    if trend_data:
        bias  = trend_data.get("bias", "NEUTRO")
        score = float(trend_data.get("score", 50))
        match = (direction == "LONG" and bias in ("LONG", "BULLISH")) or \
                (direction == "SHORT" and bias in ("SHORT", "BEARISH"))
        return min(100.0, score) if match else max(0.0, 100.0 - score)

    return 30.0  # sem dados = score baixo-neutro


def _order_block(smc_signal: dict, mm_data: dict, price: float, direction: str) -> float:
    """
    OB válido (bullish ou bearish) na zona do preço atual.
    Fontes: smc_signal['order_blocks'] + mm_data stop_hunt_zones como proxy
    """
    if not price or not direction or price <= 0:
        return 0.0

    tol = price * 0.006   # tolerância: 0.6% da zona

    # SMC Order Blocks
    if smc_signal and "order_blocks" in smc_signal:
        obs = smc_signal["order_blocks"]

        if direction == "LONG":
            ob = obs.get("bullish_ob")
            if ob and ob.get("valid"):
                ob_lo = float(ob.get("low",  0))
                ob_hi = float(ob.get("high", 0))
                if ob_lo - tol <= price <= ob_hi + tol:
                    return 100.0
                # OB próximo mas fora da zona
                if abs(price - ob_lo) <= tol * 3 or abs(price - ob_hi) <= tol * 3:
                    return 60.0

        elif direction == "SHORT":
            ob = obs.get("bearish_ob")
            if ob and ob.get("valid"):
                ob_lo = float(ob.get("low",  0))
                ob_hi = float(ob.get("high", 0))
                if ob_lo - tol <= price <= ob_hi + tol:
                    return 100.0
                if abs(price - ob_lo) <= tol * 3 or abs(price - ob_hi) <= tol * 3:
                    return 60.0

    # Fallback: stop hunt zones do MM como proxy de OB
    if mm_data:
        for zone in mm_data.get("stop_hunt_zones", []):
            lo = float(zone.get("low", 0))
            hi = float(zone.get("high", 0))
            if lo - tol <= price <= hi + tol:
                return 55.0

    return 0.0


def _fvg(smc_signal: dict, price: float, direction: str) -> float:
    """
    FVG (Fair Value Gap) não preenchido, sobreposto ou adjacente ao preço.
    Fonte: smc_signal['fvg']
    """
    if not price or not direction or not smc_signal:
        return 0.0

    fvg_data = smc_signal.get("fvg", {})
    tol = price * 0.010  # 1% de tolerância

    if direction == "LONG":
        fvg = fvg_data.get("nearest_bull_fvg")
        if fvg:
            bot = float(fvg.get("low",    fvg.get("bottom", 0)))
            top = float(fvg.get("high",   fvg.get("top",    0)))
            if top <= 0:
                return 0.0
            if bot - tol <= price <= top + tol:
                return 100.0   # dentro do FVG
            if abs(price - bot) <= tol * 2 or abs(price - top) <= tol * 2:
                return 65.0    # adjacente ao FVG

    elif direction == "SHORT":
        fvg = fvg_data.get("nearest_bear_fvg")
        if fvg:
            bot = float(fvg.get("low",    fvg.get("bottom", 0)))
            top = float(fvg.get("high",   fvg.get("top",    0)))
            if top <= 0:
                return 0.0
            if bot - tol <= price <= top + tol:
                return 100.0
            if abs(price - bot) <= tol * 2 or abs(price - top) <= tol * 2:
                return 65.0

    return 0.0


def _recent_sweep(mm_data: dict, direction: str) -> float:
    """
    Varredura de pool de liquidez recente, alinhada com a direção.
    sweep_bias LONG = swept lows → bullish (validado)
    sweep_bias SHORT = swept highs → bearish (validado)
    Fonte: mm_data['sweep_bias'] + mm_data['sweep_strength']
    """
    if not mm_data or not direction:
        return 0.0

    sweep_bias = mm_data.get("sweep_bias", "NEUTRO")
    sweep_str  = float(mm_data.get("sweep_strength", 0))

    if sweep_bias == "NEUTRO" or sweep_str <= 0:
        return 0.0

    alinhado = (direction == "LONG" and sweep_bias == "LONG") or \
               (direction == "SHORT" and sweep_bias == "SHORT")

    if alinhado:
        return min(100.0, 30.0 + sweep_str * 0.7)  # floor 30 + boost por força

    return 0.0


def _liquidations(liq_scoring: dict, direction: str) -> float:
    """
    Pico de liquidações na direção compatível com o sinal.
    LONG signal: precisa de short liquidations (bias=LONG no engine).
    SHORT signal: precisa de long liquidations (bias=SHORT no engine).
    Fonte: btc_liquidation_engine.get_for_scoring()
    """
    if not liq_scoring or not direction:
        return 20.0  # sem dados → score baixo-neutro

    if not liq_scoring.get("connected", False):
        return 20.0  # WS desconectado → score neutro baixo

    bias     = liq_scoring.get("bias", "NEUTRAL")
    strength = float(liq_scoring.get("strength", 0))
    total_5m = float(liq_scoring.get("total_5m", 0))

    if total_5m < 0.3:   # < $300k em 5min → atividade insuficiente
        return 20.0

    alinhado = (direction == "LONG" and bias == "LONG") or \
               (direction == "SHORT" and bias == "SHORT")

    if alinhado:
        return min(100.0, 35.0 + strength * 0.65)

    return 0.0


def _volume(volume_data: dict, direction: str) -> float:
    """
    Volume confirmando a direção do sinal.
    Score já normalizado 0-100 pelo volume.py.
    """
    if not volume_data or not direction:
        return 50.0

    bias  = volume_data.get("bias", "NEUTRO")
    score = float(volume_data.get("score", 50))

    alinhado = (direction == "LONG" and bias in ("LONG", "BULLISH")) or \
               (direction == "SHORT" and bias in ("SHORT", "BEARISH"))
    contrario = (direction == "LONG" and bias in ("SHORT", "BEARISH")) or \
                (direction == "SHORT" and bias in ("LONG", "BULLISH"))

    if alinhado:
        return min(100.0, score)
    elif contrario:
        return max(0.0, 100.0 - score)
    return 50.0


# ─── Função principal ────────────────────────────────────────────────────────

def detect_rare_setup(
    smc_signal:   dict,
    mm_data:      dict,
    liq_scoring:  dict,
    volume_data:  dict,
    trend_data:   dict,
    price:        float,
    direction:    str,
) -> dict:
    """
    Detecta Setup Raro (Cap. 3) combinando 6 fatores independentes.

    Args:
        smc_signal:  saída de SmartMoneyEngine.analyze()
        mm_data:     saída de run_market_maker_analysis()
        liq_scoring: saída de btc_liquidation_engine.get_for_scoring()
        volume_data: saída de volume.analyze(df)
        trend_data:  saída de trend.analyze(df)
        price:       preço atual
        direction:   "LONG" | "SHORT" | "AGUARDANDO"

    Returns:
        {
            "rare_setup":     bool,
            "score":          float (0-100),
            "components":     {nome: score, ...},
            "factors_active": [lista dos fatores com score >= 50],
            "setup_type":     str ("OB+FVG+Sweep", "Sweep+Liq+MTF", etc.),
            "signal_bonus":   float (0-100, para uso no score final)
        }
    """
    if not direction or direction == "AGUARDANDO":
        return {
            "rare_setup":     False,
            "score":          0.0,
            "components":     {},
            "factors_active": [],
            "setup_type":     "NENHUM",
            "signal_bonus":   0.0,
        }

    raw = {
        "htf_bias":     _htf_bias(smc_signal, trend_data, direction),
        "order_block":  _order_block(smc_signal, mm_data, price, direction),
        "fvg":          _fvg(smc_signal, price, direction),
        "recent_sweep": _recent_sweep(mm_data, direction),
        "liquidations": _liquidations(liq_scoring, direction),
        "volume":       _volume(volume_data, direction),
    }

    # Score ponderado 0-100
    total_w = sum(RSD_WEIGHTS.values())  # = 100
    score = sum(raw[k] * (RSD_WEIGHTS[k] / total_w) for k in RSD_WEIGHTS)
    score = max(0.0, min(100.0, score))

    # Fatores ativos (>= 50)
    active = [k for k, v in raw.items() if v >= 50.0]

    rare = (score >= RARE_THRESHOLD) and (len(active) >= MIN_FACTORS_ACTIVE)

    # Classifica o tipo de setup (do mais poderoso ao menos)
    setup_type = "NENHUM"
    if rare:
        if "order_block" in active and "fvg" in active and "recent_sweep" in active:
            setup_type = "OB+FVG+Sweep"          # Cap. 3: mais poderoso
        elif "recent_sweep" in active and "liquidations" in active and "htf_bias" in active:
            setup_type = "Sweep+Liq+MTF"
        elif "order_block" in active and "liquidations" in active:
            setup_type = "OB+Liq"
        elif "fvg" in active and "recent_sweep" in active:
            setup_type = "FVG+Sweep"
        else:
            setup_type = "+".join(f[:3].upper() for f in active[:3])

    # Bonus para fórmula final (Cap. 7: bonus_raro * 0.1)
    # Exportado como 0-100 para uso direto na fórmula ponderada
    signal_bonus = round(score, 1) if rare else 0.0

    return {
        "rare_setup":     rare,
        "score":          round(score, 1),
        "components":     {k: round(v, 1) for k, v in raw.items()},
        "factors_active": active,
        "setup_type":     setup_type,
        "signal_bonus":   signal_bonus,
    }
