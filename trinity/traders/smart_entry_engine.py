"""
smart_entry_engine.py — Motor de Entrada Inteligente com Plano A/B

Calcula pontos de entrada otimizados usando 4 fontes de dados:
  1. Orderbook — bid walls (suporte) e ask clusters (resistência)
  2. Klines — swing lows (suporte) e swing highs (resistência)
  3. Gravity detector — target_price e bid_wall_support
  4. High/Low 24h — range do dia

Cada nível recebe SCORE DE CONFLUÊNCIA:
  - Confirmado por 1 fonte: score 1 (fraco)
  - Confirmado por 2 fontes: score 2 (moderado)
  - Confirmado por 3+ fontes: score 3 (forte)
  Nível com maior confluência é usado.

Auto-seleção Plano A vs B baseada em:
  - Contexto do sinal (BOMBA_ARMADA → A, SQUEEZE → B)
  - Distância do suporte (suporte muito próximo → A)
  - Funding (extremo + flat → A sempre)

Fail-open: se qualquer parte falhar, retorna plano com preço de mercado.

Uso:
  from trinity.traders.smart_entry_engine import calculate_smart_entry
  plans = calculate_smart_entry(coin_data, "LONG", "SQUEEZE_ATIVO", 8.0)
  print(plans["recommended_plan"])  # "A" ou "B"
  print(plans["plan_a"]["entry"])   # preço de entrada do plano A
"""
import logging
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)


def calculate_smart_entry(
    coin_data: dict,
    direction: str = "LONG",
    context: str = "",
    expected_move_pct: float = 0.0,
    gravity_result: dict = None,
) -> dict:
    """
    Calcula Plano A (suporte/conservador) e Plano B (breakout/agressivo).

    Args:
        coin_data:         dict do scanner com price, orderbook, klines_15m, high/low_24h
        direction:         "LONG" ou "SHORT"
        context:           contexto do sinal pra auto-seleção (BOMBA_ARMADA, SQUEEZE_ATIVO, etc)
        expected_move_pct: move esperado em % (do scoring engine)
        gravity_result:    output do gravity/collapse detector (optional)

    Returns:
        {
            "recommended_plan":     "A" ou "B",
            "recommendation_reason": str,
            "plan_a": {
                "label":       str,    # "ENTRY NO SUPORTE" etc
                "entry":       float,
                "stop":        float,
                "tp1":         float,
                "tp2":         float,
                "tp3":         float,
                "stop_pct":    float,  # distância % do entry ao stop
                "rr_ratio":    float,  # Risk:Reward (1:X)
                "instruction": str,    # "Ordem limite em $X"
            },
            "plan_b": { ... },
        }
    """
    try:
        price = float(coin_data.get("price", 0))
        if price <= 0:
            return _default_plans(0, direction, expected_move_pct)

        high_24h = float(coin_data.get("high_24h", 0) or 0)
        low_24h  = float(coin_data.get("low_24h",  0) or 0)
        if high_24h <= 0: high_24h = price * 1.03
        if low_24h  <= 0: low_24h  = price * 0.97

        ob     = coin_data.get("orderbook", {}) or {}
        klines = coin_data.get("klines_15m", []) or []

        # ── Coletar níveis de todas as fontes ─────────────────────────────
        supports: List[Dict] = []
        resistances: List[Dict] = []

        # Fonte 1: Orderbook
        ob_sup, ob_sup_str = _ob_support(ob, price)
        ob_res, ob_res_str = _ob_resistance(ob, price)
        if ob_sup > 0:
            supports.append({"price": ob_sup, "source": "orderbook", "strength": ob_sup_str})
        if ob_res > 0:
            resistances.append({"price": ob_res, "source": "orderbook", "strength": ob_res_str})

        # Fonte 2: Klines (swing highs/lows)
        kl_sup = _kline_swing_low(klines, price)
        kl_res = _kline_swing_high(klines, price)
        if kl_sup > 0:
            supports.append({"price": kl_sup, "source": "klines", "strength": 1.0})
        if kl_res > 0:
            resistances.append({"price": kl_res, "source": "klines", "strength": 1.0})

        # Fonte 3: High/Low 24h
        if low_24h < price * 0.995:
            supports.append({"price": low_24h, "source": "24h_low", "strength": 0.8})
        if high_24h > price * 1.005:
            resistances.append({"price": high_24h, "source": "24h_high", "strength": 0.8})

        # Fonte 4: Gravity detector (opcional)
        if gravity_result:
            grav_target = float(gravity_result.get("target_price", 0) or 0)
            if grav_target > price * 1.005:
                resistances.append({"price": grav_target, "source": "gravity", "strength": 1.2})
            elif 0 < grav_target < price * 0.995:
                supports.append({"price": grav_target, "source": "gravity", "strength": 1.2})

        # ── Consolidar com confluência ────────────────────────────────────
        best_support    = _best_level(supports, price, "below")
        best_resistance = _best_level(resistances, price, "above")

        # Fallback se nenhum nível encontrado
        if best_support    <= 0: best_support    = price * 0.980
        if best_resistance <= 0: best_resistance = price * 1.025

        # ── Calcular planos ───────────────────────────────────────────────
        move = max(expected_move_pct, 4.0)

        if direction == "LONG":
            plan_a = _plan_a_long(price, best_support, best_resistance, move, resistances)
            plan_b = _plan_b_long(price, best_support, best_resistance, move, resistances)
        else:
            plan_a = _plan_a_short(price, best_support, best_resistance, move, supports)
            plan_b = _plan_b_short(price, best_support, best_resistance, move, supports)

        # ── Auto-seleção ──────────────────────────────────────────────────
        recommended, reason = _auto_select(context, direction, price, best_support, best_resistance)

        log.debug(
            f"[SmartEntry] {direction} ${price:.5g} sup=${best_support:.5g} "
            f"res=${best_resistance:.5g} → plan {recommended} ({context})"
        )

        return {
            "recommended_plan":      recommended,
            "recommendation_reason": reason,
            "plan_a":                plan_a,
            "plan_b":                plan_b,
        }

    except Exception as e:
        log.debug(f"[SmartEntry] Erro: {e}")
        return _default_plans(float(coin_data.get("price", 0)), direction, expected_move_pct)


# ══════════════════════════════════════════════════════════════════════════════
# DETECÇÃO DE NÍVEIS — ORDERBOOK
# ══════════════════════════════════════════════════════════════════════════════

def _ob_support(ob: dict, price: float) -> Tuple[float, float]:
    """
    Encontra o suporte mais forte do orderbook (maior bid wall em USD).
    Procura nos primeiros 4% abaixo do preço.
    Retorna (price, strength_normalized).
    """
    bids = ob.get("bids", [])
    if not bids:
        return 0.0, 0.0

    floor     = price * 0.96
    best_price = 0.0
    best_usd   = 0.0
    total_usd  = 0.0
    n          = 0

    for b in bids:
        try:
            bp  = float(b[0])
            bq  = float(b[1])
            if bp < floor:
                break
            usd = bp * bq
            total_usd += usd
            n += 1
            if usd > best_usd:
                best_usd   = usd
                best_price = bp
        except (ValueError, TypeError, IndexError):
            continue

    avg      = total_usd / max(n, 1)
    strength = min(3.0, best_usd / max(avg, 1)) if avg > 0 else 1.0
    return best_price, strength


def _ob_resistance(ob: dict, price: float) -> Tuple[float, float]:
    """
    Encontra a resistência mais forte do orderbook (maior ask wall em USD).
    Procura nos primeiros 5% acima do preço.
    """
    asks = ob.get("asks", [])
    if not asks:
        return 0.0, 0.0

    ceiling   = price * 1.05
    best_price = 0.0
    best_usd   = 0.0
    total_usd  = 0.0
    n          = 0

    for a in asks:
        try:
            ap  = float(a[0])
            aq  = float(a[1])
            if ap > ceiling:
                break
            usd = ap * aq
            total_usd += usd
            n += 1
            if usd > best_usd:
                best_usd   = usd
                best_price = ap
        except (ValueError, TypeError, IndexError):
            continue

    avg      = total_usd / max(n, 1)
    strength = min(3.0, best_usd / max(avg, 1)) if avg > 0 else 1.0
    return best_price, strength


# ══════════════════════════════════════════════════════════════════════════════
# DETECÇÃO DE NÍVEIS — KLINES (swing highs/lows)
# ══════════════════════════════════════════════════════════════════════════════

def _kline_swing_low(klines: list, price: float) -> float:
    """
    Encontra swing low mais recente: vela cuja mínima é menor que as 2 vizinhas.
    Procura nas últimas 20 velas. Retorna 0 se não encontrar.
    """
    if not klines or len(klines) < 5:
        return 0.0

    recent = klines[-20:]
    lows = []
    for k in recent:
        try:
            lows.append(float(k[3]))
        except (ValueError, TypeError, IndexError):
            lows.append(0)

    # Swing lows: low[i] < low[i-1] AND low[i] < low[i+1]
    swing_lows = []
    for i in range(1, len(lows) - 1):
        if lows[i] > 0 and lows[i] < lows[i-1] and lows[i] < lows[i+1]:
            if lows[i] < price:
                swing_lows.append(lows[i])

    if not swing_lows:
        valid = [l for l in lows[-10:] if 0 < l < price]
        return max(valid) if valid else 0.0

    swing_lows.sort(reverse=True)
    return swing_lows[0]   # Mais próximo do preço


def _kline_swing_high(klines: list, price: float) -> float:
    """
    Encontra swing high mais recente: vela cuja máxima é maior que as 2 vizinhas.
    """
    if not klines or len(klines) < 5:
        return 0.0

    recent = klines[-20:]
    highs = []
    for k in recent:
        try:
            highs.append(float(k[2]))
        except (ValueError, TypeError, IndexError):
            highs.append(0)

    swing_highs = []
    for i in range(1, len(highs) - 1):
        if highs[i] > 0 and highs[i] > highs[i-1] and highs[i] > highs[i+1]:
            if highs[i] > price:
                swing_highs.append(highs[i])

    if not swing_highs:
        valid = [h for h in highs[-10:] if h > price]
        return min(valid) if valid else 0.0

    swing_highs.sort()
    return swing_highs[0]   # Mais próximo do preço


# ══════════════════════════════════════════════════════════════════════════════
# CONFLUÊNCIA DE NÍVEIS
# ══════════════════════════════════════════════════════════════════════════════

def _best_level(levels: List[Dict], price: float, side: str) -> float:
    """
    Consolida níveis de múltiplas fontes usando confluência.

    Níveis dentro de 0.8% um do outro são considerados o MESMO nível.
    Confluência = quantas fontes independentes confirmam o mesmo nível.
    O nível com maior confluência + mais próximo do preço vence.
    """
    if not levels:
        return 0.0

    # Filtrar: suportes abaixo do preço, resistências acima
    if side == "below":
        valid = [l for l in levels if 0 < l["price"] < price * 0.999]
    else:
        valid = [l for l in levels if l["price"] > price * 1.001]

    if not valid:
        return 0.0

    # Agrupar por proximidade (0.8% = mesmo nível)
    band     = price * 0.008
    clusters: List[Dict] = []

    for lv in sorted(valid, key=lambda x: x["price"], reverse=(side == "below")):
        merged = False
        for cl in clusters:
            if abs(lv["price"] - cl["center"]) <= band:
                cl["sources"].append(lv["source"])
                cl["total_strength"] += lv["strength"]
                cl["count"] += 1
                cl["center"] = (cl["center"] * (cl["count"] - 1) + lv["price"]) / cl["count"]
                merged = True
                break
        if not merged:
            clusters.append({
                "center":         lv["price"],
                "sources":        [lv["source"]],
                "total_strength": lv["strength"],
                "count":          1,
            })

    if not clusters:
        return 0.0

    # Scoring: confluência × strength × proximidade
    for cl in clusters:
        confluence = len(set(cl["sources"]))
        dist       = abs(cl["center"] - price) / price
        proximity  = max(0.1, 1.0 - dist * 10)
        cl["score"] = confluence * cl["total_strength"] * proximity

    best = max(clusters, key=lambda c: c["score"])
    return best["center"]


# ══════════════════════════════════════════════════════════════════════════════
# CÁLCULO DOS PLANOS — LONG
# ══════════════════════════════════════════════════════════════════════════════

def _plan_a_long(price: float, support: float, resistance: float,
                 move_pct: float, all_resistances: list) -> dict:
    """Plano A LONG: Entry no suporte, stop abaixo, TPs nas resistências."""
    entry = support

    # Stop: entre 1.2% e 3% abaixo do entry
    stop_dist_pct = max(1.2, min(3.0, (price - support) / price * 100 * 0.5))
    stop = entry * (1 - stop_dist_pct / 100)

    # TPs baseados no expected_move a partir do entry
    tp1 = entry * (1 + move_pct * 0.35 / 100)
    tp2 = entry * (1 + move_pct * 0.65 / 100)
    tp3 = entry * (1 + move_pct / 100)

    # Se temos resistências reais, usar como TP
    real_res = sorted([r["price"] for r in all_resistances if r["price"] > entry * 1.01])
    if real_res:
        if len(real_res) >= 1: tp1 = max(tp1, real_res[0])
        if len(real_res) >= 2: tp2 = max(tp2, real_res[1])
        if len(real_res) >= 3: tp3 = max(tp3, real_res[2])

    return _build_plan(
        label       = "ENTRY NO SUPORTE",
        instruction = f"Ordem limite em {_fp(entry)} — aguardar pullback",
        entry=entry, stop=stop, tp1=tp1, tp2=tp2, tp3=tp3,
    )


def _plan_b_long(price: float, support: float, resistance: float,
                 move_pct: float, all_resistances: list) -> dict:
    """Plano B LONG: Entry no breakout da resistência, stop abaixo do preço atual."""
    entry = resistance

    # Stop: entre preço atual e suporte — se voltar, sai
    stop = max(price * 0.985, support, entry * 0.975)
    if stop >= entry:
        stop = entry * 0.975

    tp1 = entry * (1 + move_pct * 0.35 / 100)
    tp2 = entry * (1 + move_pct * 0.65 / 100)
    tp3 = entry * (1 + move_pct / 100)

    # Resistências ACIMA do breakout como TPs
    higher_res = sorted([r["price"] for r in all_resistances if r["price"] > entry * 1.01])
    if higher_res:
        if len(higher_res) >= 1: tp1 = max(tp1, higher_res[0])
        if len(higher_res) >= 2: tp2 = max(tp2, higher_res[1])

    return _build_plan(
        label       = "ENTRY EM BREAKOUT",
        instruction = f"Entrar quando romper {_fp(entry)} com volume",
        entry=entry, stop=stop, tp1=tp1, tp2=tp2, tp3=tp3,
    )


# ══════════════════════════════════════════════════════════════════════════════
# CÁLCULO DOS PLANOS — SHORT
# ══════════════════════════════════════════════════════════════════════════════

def _plan_a_short(price: float, support: float, resistance: float,
                  move_pct: float, all_supports: list) -> dict:
    """Plano A SHORT: Entry na resistência (bounce pra cima), stop acima."""
    entry = resistance

    stop_dist_pct = max(1.2, min(3.0, (resistance - price) / price * 100 * 0.5))
    stop = entry * (1 + stop_dist_pct / 100)

    tp1 = entry * (1 - move_pct * 0.35 / 100)
    tp2 = entry * (1 - move_pct * 0.65 / 100)
    tp3 = entry * (1 - move_pct / 100)

    real_sup = sorted([s["price"] for s in all_supports if s["price"] < entry * 0.99], reverse=True)
    if real_sup:
        if len(real_sup) >= 1: tp1 = min(tp1, real_sup[0])
        if len(real_sup) >= 2: tp2 = min(tp2, real_sup[1])
        if len(real_sup) >= 3: tp3 = min(tp3, real_sup[2])

    return _build_plan(
        label       = "ENTRY NA RESISTÊNCIA",
        instruction = f"Ordem limite em {_fp(entry)} — aguardar bounce",
        entry=entry, stop=stop, tp1=tp1, tp2=tp2, tp3=tp3,
    )


def _plan_b_short(price: float, support: float, resistance: float,
                  move_pct: float, all_supports: list) -> dict:
    """Plano B SHORT: Entry no breakdown do suporte, stop acima do preço atual."""
    entry = support

    stop = min(price * 1.015, resistance, entry * 1.025)
    if stop <= entry:
        stop = entry * 1.025

    tp1 = entry * (1 - move_pct * 0.35 / 100)
    tp2 = entry * (1 - move_pct * 0.65 / 100)
    tp3 = entry * (1 - move_pct / 100)

    lower_sup = sorted([s["price"] for s in all_supports if s["price"] < entry * 0.99], reverse=True)
    if lower_sup:
        if len(lower_sup) >= 1: tp1 = min(tp1, lower_sup[0])
        if len(lower_sup) >= 2: tp2 = min(tp2, lower_sup[1])

    return _build_plan(
        label       = "ENTRY EM BREAKDOWN",
        instruction = f"Entrar quando perder {_fp(entry)} com volume",
        entry=entry, stop=stop, tp1=tp1, tp2=tp2, tp3=tp3,
    )


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-SELEÇÃO
# ══════════════════════════════════════════════════════════════════════════════

def _auto_select(context: str, direction: str, price: float,
                 support: float, resistance: float) -> Tuple[str, str]:
    """
    Seleciona Plano A ou B baseado no contexto do sinal.

    Regras de prioridade (primeira que bater):
    1. BOMBA_ARMADA → A (flat + pressão acumulada = tempo pra esperar suporte)
    2. SQUEEZE_ATIVO → B (momentum explodiu, não vai voltar)
    3. COMPRESSÃO → A (preço lateral, suporte claro)
    4. OVEREXTENSION → B (confirmação de reversão)
    5. Suporte/resistência muito próximo (< 1%) → A
    6. Default → B (breakout mais confiável com momentum)
    """
    ctx = (context or "").upper()

    a_patterns = [
        "BOMBA_ARMADA", "BOMBA ARMADA", "COMPRESSAO", "COMPRESSION",
        "HIDDEN ACCUMULATION", "SILENT", "ACUMULANDO", "FLAT",
    ]
    b_patterns = [
        "SQUEEZE_ATIVO", "SQUEEZE ATIVO", "SQUEEZE", "SHORT SQUEEZE",
        "COMPRESSION LAUNCH", "OVEREXTENSION", "OVEREXT", "BREAKOUT",
        "LIQUIDACAO", "MOMENTUM", "VOLUME BREAKOUT",
    ]

    for pat in a_patterns:
        if pat in ctx:
            return "A", f"Plano A — {context} → suporte como entry ideal"

    for pat in b_patterns:
        if pat in ctx:
            return "B", f"Plano B — {context} → breakout confirma o movimento"

    # Heurística de distância
    if direction == "LONG" and support > 0:
        if (price - support) / price * 100 < 1.0:
            return "A", "Plano A — suporte a menos de 1%, pullback provável"

    if direction == "SHORT" and resistance > 0:
        if (resistance - price) / price * 100 < 1.0:
            return "A", "Plano A — resistência a menos de 1%, bounce provável"

    return "B", "Plano B — breakout como confirmação de movimento"


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _build_plan(label: str, instruction: str,
                entry: float, stop: float, tp1: float, tp2: float, tp3: float) -> dict:
    """Constrói dict do plano com R:R calculado."""
    risk   = abs(entry - stop)
    reward = abs(tp2 - entry)   # R:R baseado no TP2
    rr     = round(reward / risk, 1) if risk > 0 else 0.0
    stop_pct = round(abs(entry - stop) / entry * 100, 2) if entry > 0 else 0.0

    return {
        "label":       label,
        "entry":       _rnd(entry),
        "stop":        _rnd(stop),
        "tp1":         _rnd(tp1),
        "tp2":         _rnd(tp2),
        "tp3":         _rnd(tp3),
        "stop_pct":    stop_pct,
        "rr_ratio":    rr,
        "instruction": instruction,
    }


def _rnd(v: float) -> float:
    """Round inteligente baseado na magnitude do preço."""
    if v <= 0:    return 0.0
    if v < 0.0001: return round(v, 8)
    if v < 0.01:   return round(v, 6)
    if v < 1:      return round(v, 5)
    if v < 10:     return round(v, 4)
    if v < 100:    return round(v, 3)
    return round(v, 2)


def _fp(p: float) -> str:
    """Formata preço pra display."""
    if p <= 0:    return "—"
    if p < 0.001: return f"${p:,.7f}"
    if p < 0.01:  return f"${p:,.6f}"
    if p < 1:     return f"${p:,.5f}"
    if p < 10:    return f"${p:,.4f}"
    if p < 100:   return f"${p:,.3f}"
    return f"${p:,.2f}"


def _default_plans(price: float, direction: str, move_pct: float) -> dict:
    """Fallback quando não há dados suficientes — usa market order."""
    if price <= 0: price = 1.0
    move = max(move_pct, 4.0)

    if direction == "LONG":
        plan = _build_plan(
            "MARKET ORDER", f"Entry a mercado em {_fp(price)}",
            price, price * 0.975,
            price * (1 + move * 0.35 / 100),
            price * (1 + move * 0.65 / 100),
            price * (1 + move / 100),
        )
    else:
        plan = _build_plan(
            "MARKET ORDER", f"Entry a mercado em {_fp(price)}",
            price, price * 1.025,
            price * (1 - move * 0.35 / 100),
            price * (1 - move * 0.65 / 100),
            price * (1 - move / 100),
        )

    return {
        "recommended_plan":      "B",
        "recommendation_reason": "Fallback — dados de nível insuficientes",
        "plan_a": {**plan, "label": "MARKET (sem dados de suporte)"},
        "plan_b": {**plan, "label": "MARKET (sem dados de resistência)"},
    }
