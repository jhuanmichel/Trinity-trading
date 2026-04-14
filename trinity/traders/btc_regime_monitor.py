"""
btc_regime_monitor.py — BTC Regime Engine v3

Não apenas mede o regime BTC — detecta TRANSIÇÕES de regime.
A transição NEUTRAL → BULL é mais valiosa que BULL sustentado,
porque as alts ainda não reagiram.

Análise multi-camada:
  Layer 1: Ticker (preço, funding, OI) — estado atual
  Layer 2: Klines multi-timeframe (momentum 15m/1h/4h) — velocidade
  Layer 3: Binance L/S (posicionamento) — combustível
  Layer 4: Transição de regime (comparação com estado anterior) — timing

Regimes:
  STRONG_BULL  → múltiplas confirmações bullish (3+)
  BULL         → bias comprador moderado (2 confirmações)
  NEUTRAL      → sem direção clara
  BEAR         → bias vendedor moderado
  STRONG_BEAR  → múltiplas confirmações bearish (3+)

Transições (o sinal de ouro):
  NEUTRAL → BULL     = "BTC REGIME SHIFT BULLISH"  → boost máximo para pump
  NEUTRAL → BEAR     = "BTC REGIME SHIFT BEARISH"  → boost máximo para crash
  BEAR → STRONG_BEAR = "BTC SELL-OFF ACCELERATING" → boost crash
  BULL → STRONG_BULL = "BTC BREAKOUT ACCELERATING" → boost pump
  BULL → NEUTRAL     = "BTC MOMENTUM FADING"       → reduzir confiança pump
  BEAR → NEUTRAL     = "BTC SELLING EXHAUSTION"    → reduzir confiança crash
"""
import logging
import time
from typing import Dict, Optional, Tuple

import requests

log = logging.getLogger(__name__)

MEXC_FUTURES = "https://contract.mexc.com"
BINANCE_FAPI = "https://fapi.binance.com"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)",
    "Accept": "application/json",
}

# ── Estado persistente em RAM ─────────────────────────────────────────────
_regime_cache: Dict     = {"data": None, "ts": 0.0}
_previous_regime: Dict  = {"direction": "NEUTRAL", "ts": 0.0}
_funding_history: list  = []  # últimos 10 funding rates para detectar aceleração
CACHE_TTL               = 30
TRANSITION_MEMORY_S     = 300  # transição é "recente" por 5 minutos

# ── FRED API (macro conditions) ───────────────────────────────────────────
FRED_BASE      = "https://api.stlouisfed.org/fred/series/observations"
_macro_cache: Dict = {"data": None, "ts": 0.0}
MACRO_CACHE_TTL    = 3600  # 1 hora (dados são semanais — não precisa buscar a cada ciclo)

# ── Bull Market Support Band ───────────────────────────────────────────────
_bull_band_cache: Dict = {"data": None, "ts": 0.0}
BULL_BAND_CACHE_TTL    = 3600  # 1h

# ── Sazonalidade mensal BTC — média 2015-2025 ─────────────────────────────
_MONTHLY_SEASONALITY = {
    1: 1.12, 2: 1.08, 3: 1.03, 4: 1.06,
    5: 0.92, 6: 0.87, 7: 0.90, 8: 0.91,
    9: 0.86, 10: 1.12, 11: 1.14, 12: 1.08,
}


def get_btc_regime() -> dict:
    """
    Retorna regime BTC com detecção de transição.
    Cache de 30s — nunca faz request por candidato.

    Returns:
        {
            "direction":        str,    # STRONG_BULL/BULL/NEUTRAL/BEAR/STRONG_BEAR
            "strength":         float,  # 0-100
            "bias":             str,    # PUMP/CRASH/NEUTRAL
            "transition":       str,    # "" ou "REGIME_SHIFT_BULLISH" etc
            "transition_boost": float,  # 1.0 (sem transição) a 1.40 (transição forte)
            "funding_rate":     float,
            "funding_ann":      float,
            "funding_accel":    str,    # ACCELERATING/STABLE/DECELERATING
            "price_change_pct": float,  # 24h
            "momentum_15m":     float,  # % variação últimos 15min
            "momentum_1h":      float,  # % variação última hora
            "momentum_4h":      float,  # % variação últimas 4 horas
            "oi_usd":           float,
            "ls_ratio":         float,
            "signals":          list,   # sinais ativos descritivos
            "bull_score":       float,
            "bear_score":       float,
            "confirmations":    int,    # quantas camadas concordam (0-5)
        }
    """
    now = time.time()
    if _regime_cache["data"] and (now - _regime_cache["ts"]) < CACHE_TTL:
        return _regime_cache["data"]

    try:
        regime = _full_analysis()
        _regime_cache.update({"data": regime, "ts": now})
        return regime
    except Exception as e:
        log.warning(f"[BTCRegime] Erro na análise: {e}")
        return _default_regime()


def _full_analysis() -> dict:
    """Análise completa multi-camada do BTC."""
    global _previous_regime

    # ── Layer 1: Ticker MEXC ──────────────────────────────────────────────
    ticker = _get_btc_ticker()
    if not ticker:
        return _default_regime()

    price        = float(ticker.get("lastPrice", 0))
    funding_rate = float(ticker.get("fundingRate", 0))
    rise_fall    = float(ticker.get("riseFallRate", 0))
    pct_24h      = rise_fall * 100
    hold_vol     = float(ticker.get("holdVol", 0))
    oi_usd       = hold_vol * price
    amount24     = float(ticker.get("amount24", 0))

    rfr = ticker.get("riseFallRates", {})
    r7_pct  = float(rfr.get("r7", 0)) * 100 if isinstance(rfr, dict) else 0.0
    r30_pct = float(rfr.get("r30", 0)) * 100 if isinstance(rfr, dict) else 0.0

    funding_ann = funding_rate * 3 * 365 * 100

    # ── Layer 2: Momentum multi-timeframe via klines ──────────────────────
    momentum_15m, momentum_1h, momentum_4h = _get_multi_tf_momentum()

    # ── Layer 3: Binance L/S ratio ────────────────────────────────────────
    ls_ratio = _get_btc_ls_ratio()

    # ── Layer 4: Funding acceleration ─────────────────────────────────────
    funding_accel = _track_funding_acceleration(funding_rate)

    # ── Scoring por camadas independentes ──────────────────────────────────
    signals = []
    bull_points = 0.0
    bear_points = 0.0
    bull_confirmations = 0
    bear_confirmations = 0

    # ── CAMADA A: Funding rate (0-25 pts) ──────────────────────────────────
    if funding_ann < -100:
        bull_points += 25
        bull_confirmations += 1
        signals.append(f"Funding extremo negativo ({funding_ann:.0f}%/yr) — squeeze pressure")
    elif funding_ann < -40:
        bull_points += 15
        bull_confirmations += 1
        signals.append(f"Funding negativo ({funding_ann:.0f}%/yr)")
    elif funding_ann > 100:
        bear_points += 25
        bear_confirmations += 1
        signals.append(f"Funding extremo positivo ({funding_ann:.0f}%/yr) — overleveraged longs")
    elif funding_ann > 40:
        bear_points += 15
        bear_confirmations += 1
        signals.append(f"Funding positivo ({funding_ann:.0f}%/yr)")

    # Bônus por aceleração de funding
    if funding_accel == "ACCELERATING" and funding_ann < -20:
        bull_points += 8
        signals.append("Funding negativo ACELERANDO — pressão crescente")
    elif funding_accel == "ACCELERATING" and funding_ann > 20:
        bear_points += 8
        signals.append("Funding positivo ACELERANDO — risco crescente")

    # ── CAMADA B: Momentum 1h (mais importante que 24h) (0-20 pts) ─────────
    if momentum_1h > 2.5:
        bull_points += 20
        bull_confirmations += 1
        signals.append(f"Momentum 1h +{momentum_1h:.1f}% — breakout ativo")
    elif momentum_1h > 1.0:
        bull_points += 12
        bull_confirmations += 1
        signals.append(f"Momentum 1h +{momentum_1h:.1f}%")
    elif momentum_1h > 0.4:
        bull_points += 5
    elif momentum_1h < -2.5:
        bear_points += 20
        bear_confirmations += 1
        signals.append(f"Momentum 1h {momentum_1h:.1f}% — dump ativo")
    elif momentum_1h < -1.0:
        bear_points += 12
        bear_confirmations += 1
        signals.append(f"Momentum 1h {momentum_1h:.1f}%")
    elif momentum_1h < -0.4:
        bear_points += 5

    # ── CAMADA C: Momentum 15m (urgência imediata) (0-15 pts) ──────────────
    if momentum_15m > 1.5:
        bull_points += 15
        bull_confirmations += 1
        signals.append(f"Sprint 15m +{momentum_15m:.1f}% — movimento acelerado AGORA")
    elif momentum_15m > 0.6:
        bull_points += 8
    elif momentum_15m < -1.5:
        bear_points += 15
        bear_confirmations += 1
        signals.append(f"Queda 15m {momentum_15m:.1f}% — sell-off ativo AGORA")
    elif momentum_15m < -0.6:
        bear_points += 8

    # ── CAMADA D: L/S ratio (posicionamento) (0-15 pts) ────────────────────
    if ls_ratio < 0.65:
        bull_points += 15
        bull_confirmations += 1
        signals.append(f"L/S {ls_ratio:.2f} — shorts dominando, max squeeze fuel")
    elif ls_ratio < 0.80:
        bull_points += 8
    elif ls_ratio > 2.2:
        bear_points += 15
        bear_confirmations += 1
        signals.append(f"L/S {ls_ratio:.2f} — longs dominando, max liquidação fuel")
    elif ls_ratio > 1.6:
        bear_points += 8

    # ── CAMADA E: Tendência macro 24h + 7d (confirmação) (0-15 pts) ────────
    if pct_24h > 4.0 and r7_pct > 8.0:
        bull_points += 15
        bull_confirmations += 1
        signals.append(f"Tendência confirmada: 24h +{pct_24h:.1f}% / 7d +{r7_pct:.0f}%")
    elif pct_24h > 2.0 and r7_pct > 3.0:
        bull_points += 8
    elif pct_24h < -4.0 and r7_pct < -8.0:
        bear_points += 15
        bear_confirmations += 1
        signals.append(f"Downtrend confirmado: 24h {pct_24h:.1f}% / 7d {r7_pct:.0f}%")
    elif pct_24h < -2.0 and r7_pct < -3.0:
        bear_points += 8

    # ── CAMADA F: Momentum 4h (confirmação de tendência) (0-10 pts) ────────
    if momentum_4h > 4.0:
        bull_points += 10
        signals.append(f"Momentum 4h +{momentum_4h:.1f}% — tendência forte intraday")
    elif momentum_4h > 2.0:
        bull_points += 5
    elif momentum_4h < -4.0:
        bear_points += 10
        signals.append(f"Momentum 4h {momentum_4h:.1f}% — pressão vendedora intraday")
    elif momentum_4h < -2.0:
        bear_points += 5

    # ── CAMADA G: NFCI — Condições financeiras macro (0-15 pts) ─────────────
    macro = _get_macro_conditions()
    nfci_regime    = macro.get("nfci_regime", "NEUTRAL")
    nfci_direction = macro.get("nfci_direction", "STABLE")

    if nfci_regime == "RISK_ON":
        if nfci_direction == "LOOSENING":
            bull_points += 15
            bull_confirmations += 1
            signals.append(
                f"NFCI RISK ON + afrouxando ({macro['nfci_value']:.3f}) — macro bullish"
            )
        else:
            bull_points += 8
            signals.append(
                f"NFCI RISK ON ({macro['nfci_value']:.3f}) — condições frouxas"
            )
    elif nfci_regime == "RISK_OFF":
        if nfci_direction == "TIGHTENING":
            bear_points += 15
            bear_confirmations += 1
            signals.append(
                f"NFCI RISK OFF + apertando ({macro['nfci_value']:.3f}) — macro bearish"
            )
        else:
            bear_points += 8
            signals.append(
                f"NFCI RISK OFF ({macro['nfci_value']:.3f}) — condições apertadas"
            )

    # ── CAMADA H: M2 Liquidez global (0-10 pts) ─────────────────────────────
    m2_direction = macro.get("m2_direction", "STABLE")
    m2_yoy       = macro.get("m2_yoy_pct", 0)

    if m2_direction == "EXPANDING" and m2_yoy > 5:
        bull_points += 10
        bull_confirmations += 1
        signals.append(f"M2 expandindo +{m2_yoy:.1f}% YoY — liquidez crescente")
    elif m2_direction == "EXPANDING":
        bull_points += 5
        signals.append(f"M2 expandindo +{m2_yoy:.1f}% YoY")
    elif m2_direction == "CONTRACTING" and m2_yoy < -2:
        bear_points += 10
        bear_confirmations += 1
        signals.append(f"M2 contraindo {m2_yoy:.1f}% YoY — liquidez secando")
    elif m2_direction == "CONTRACTING":
        bear_points += 5
        signals.append(f"M2 contraindo {m2_yoy:.1f}% YoY")

    # ── CAMADA I: Bull Market Support Band (0-12 pts) ──────────────────────
    bull_band = _get_bull_market_support_band()
    bb_bias   = bull_band.get("bull_band_bias", "NEUTRAL")
    bb_dist   = bull_band.get("distance_pct", 0)

    if bb_bias == "BULL":
        bull_points += 12
        bull_confirmations += 1
        signals.append(f"📈 BTC ACIMA do Bull Support Band ({bb_dist:+.1f}%) — bull market")
    elif bb_bias == "BEAR":
        bear_points += 12
        bear_confirmations += 1
        signals.append(f"📉 BTC ABAIXO do Bull Support Band ({bb_dist:+.1f}%) — bear market")
    elif bb_bias == "TRANSITION":
        if bb_dist > 0:
            bull_points += 5
            signals.append(f"↗️ BTC cruzando Bull Band pra cima ({bb_dist:+.1f}%)")
        else:
            bear_points += 5
            signals.append(f"↘️ BTC cruzando Bull Band pra baixo ({bb_dist:+.1f}%)")

    # ── CAMADA J: Sazonalidade mensal (0-8 pts) ───────────────────────────
    seasonality = _get_monthly_seasonality()
    season      = seasonality.get("season", "NEUTRAL")

    if season == "STRONG":
        bull_points += 8
        signals.append(f"📅 {seasonality['description']}")
    elif season == "WEAK":
        bear_points += 8
        signals.append(f"📅 {seasonality['description']}")

    # ── Direção e classificação ────────────────────────────────────────────
    net = bull_points - bear_points
    strength = min(100, max(bull_points, bear_points))
    confirmations = max(bull_confirmations, bear_confirmations)

    if net >= 45 and confirmations >= 3:
        direction = "STRONG_BULL"
        bias = "PUMP"
    elif net >= 20 and confirmations >= 2:
        direction = "BULL"
        bias = "PUMP"
    elif net <= -45 and confirmations >= 3:
        direction = "STRONG_BEAR"
        bias = "CRASH"
    elif net <= -20 and confirmations >= 2:
        direction = "BEAR"
        bias = "CRASH"
    else:
        direction = "NEUTRAL"
        bias = "NEUTRAL"

    # ── Detecção de TRANSIÇÃO ──────────────────────────────────────────────
    transition, transition_boost = _detect_transition(direction, strength)

    # Atualizar regime anterior
    _previous_regime.update({"direction": direction, "ts": time.time()})

    log.info(
        f"[BTCRegime] {direction} strength={strength:.0f} "
        f"confirmations={confirmations} bias={bias} "
        f"transition='{transition}' boost={transition_boost:.2f} | "
        f"funding={funding_ann:.0f}%/yr 15m={momentum_15m:+.1f}% "
        f"1h={momentum_1h:+.1f}% 4h={momentum_4h:+.1f}% L/S={ls_ratio:.2f} | "
        f"NFCI={macro.get('nfci_value', 0):.3f}({macro.get('nfci_regime', '?')}) "
        f"M2={macro.get('m2_yoy_pct', 0):+.1f}%({macro.get('m2_direction', '?')})"
    )

    return {
        "direction":        direction,
        "strength":         round(strength, 1),
        "bias":             bias,
        "transition":       transition,
        "transition_boost": transition_boost,
        "funding_rate":     funding_rate,
        "funding_ann":      round(funding_ann, 1),
        "funding_accel":    funding_accel,
        "price_change_pct": round(pct_24h, 2),
        "momentum_15m":     round(momentum_15m, 2),
        "momentum_1h":      round(momentum_1h, 2),
        "momentum_4h":      round(momentum_4h, 2),
        "oi_usd":           round(oi_usd),
        "ls_ratio":         round(ls_ratio, 4),
        "signals":          signals,
        "bull_score":       round(bull_points, 1),
        "bear_score":       round(bear_points, 1),
        "confirmations":    confirmations,
        "momentum_score":   round(min(100, bull_points + bear_points), 1),
        # ── Campos macro ─────────────────────────────────────────────────
        "nfci_value":       macro.get("nfci_value", 0),
        "nfci_regime":      macro.get("nfci_regime", "NEUTRAL"),
        "nfci_direction":   macro.get("nfci_direction", "STABLE"),
        "m2_yoy_pct":       macro.get("m2_yoy_pct", 0),
        "m2_direction":     macro.get("m2_direction", "STABLE"),
        "macro_bias":       macro.get("macro_bias", "NEUTRAL"),
        "macro_strength":   macro.get("macro_strength", 0),
        # ── Campos Bull Band + Sazonalidade ──────────────────────────────
        "bull_band_sma":        bull_band.get("sma_20w", 0),
        "bull_band_ema":        bull_band.get("ema_21w", 0),
        "bull_band_bias":       bull_band.get("bull_band_bias", "NEUTRAL"),
        "bull_band_dist":       bull_band.get("distance_pct", 0),
        "seasonality_month":    seasonality.get("month", 0),
        "seasonality_mult":     seasonality.get("multiplier", 1.0),
        "seasonality_season":   seasonality.get("season", "NEUTRAL"),
    }


def _detect_transition(current_direction: str, current_strength: float) -> Tuple[str, float]:
    """
    Detecta transição de regime BTC.
    A transição é o sinal de OURO — significa que o mercado ACABOU de virar.
    Alts ainda não reagiram → janela de 5-15 min para entrar.

    Returns: (transition_name, boost_multiplier)
    """
    prev = _previous_regime.get("direction", "NEUTRAL")
    prev_ts = _previous_regime.get("ts", 0)
    now = time.time()

    # Sem regime anterior → sem transição
    if not prev_ts or prev == current_direction:
        return "", 1.0

    # Transição muito antiga → ignorar
    if (now - prev_ts) > TRANSITION_MEMORY_S:
        return "", 1.0

    # ── Transições de alto valor ───────────────────────────────────────────
    transition_map = {
        # (prev, current): (nome, boost)
        ("NEUTRAL", "STRONG_BULL"): ("BTC REGIME SHIFT BULLISH — MÁXIMO",       1.40),
        ("NEUTRAL", "BULL"):        ("BTC REGIME SHIFT BULLISH",                1.30),
        ("BEAR", "BULL"):           ("BTC REVERSÃO BULL — bears liquidando",    1.35),
        ("BEAR", "NEUTRAL"):        ("BTC SELLING EXHAUSTION",                  1.10),
        ("BULL", "STRONG_BULL"):    ("BTC BREAKOUT ACELERANDO",                 1.25),

        ("NEUTRAL", "STRONG_BEAR"): ("BTC REGIME SHIFT BEARISH — MÁXIMO",      1.40),
        ("NEUTRAL", "BEAR"):        ("BTC REGIME SHIFT BEARISH",                1.30),
        ("BULL", "BEAR"):           ("BTC REVERSÃO BEAR — longs liquidando",    1.35),
        ("BULL", "NEUTRAL"):        ("BTC MOMENTUM FADING",                     1.10),
        ("BEAR", "STRONG_BEAR"):    ("BTC SELL-OFF ACELERANDO",                 1.25),

        # Transições de enfraquecimento (reduz confiança)
        ("STRONG_BULL", "BULL"):    ("BTC bull desacelerando",                  0.90),
        ("STRONG_BULL", "NEUTRAL"): ("BTC bull colapsando",                     0.80),
        ("STRONG_BEAR", "BEAR"):    ("BTC bear desacelerando",                  0.90),
        ("STRONG_BEAR", "NEUTRAL"): ("BTC bear colapsando",                     0.80),
    }

    key = (prev, current_direction)
    if key in transition_map:
        name, boost = transition_map[key]
        log.info(f"[BTCRegime] TRANSIÇÃO DETECTADA: {prev} → {current_direction} = {name} (boost={boost})")
        return name, boost

    return "", 1.0


def _track_funding_acceleration(current_funding: float) -> str:
    """
    Tracked últimos 10 funding rates para detectar aceleração.
    ACCELERATING = funding ficando mais extremo (positivo ou negativo crescendo)
    DECELERATING = funding voltando ao neutro
    STABLE = sem mudança significativa
    """
    _funding_history.append(current_funding)
    if len(_funding_history) > 10:
        _funding_history.pop(0)

    if len(_funding_history) < 3:
        return "STABLE"

    recent_3 = _funding_history[-3:]
    # Verificar se está ficando mais negativo (bullish acceleration)
    if all(recent_3[i] < recent_3[i-1] for i in range(1, len(recent_3))):
        return "ACCELERATING"
    # Verificar se está ficando mais positivo (bearish acceleration)
    if all(recent_3[i] > recent_3[i-1] for i in range(1, len(recent_3))):
        return "ACCELERATING"
    # Voltando ao zero
    if abs(recent_3[-1]) < abs(recent_3[0]):
        return "DECELERATING"

    return "STABLE"


# ── Helpers de dados ──────────────────────────────────────────────────────

def _get_btc_ticker() -> dict:
    try:
        r = requests.get(
            f"{MEXC_FUTURES}/api/v1/contract/ticker",
            headers=_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        tickers = data.get("data", data) if isinstance(data, dict) else data
        for t in tickers:
            if t.get("symbol") == "BTC_USDT":
                return t
    except Exception as e:
        log.debug(f"[BTCRegime] Ticker error: {e}")
    return {}


def _get_btc_ls_ratio() -> float:
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": "BTCUSDT", "period": "5m", "limit": 1},
            headers=_HEADERS,
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                item = data[0]
                long_pct = float(item.get("longAccount", 0.5))
                short_pct = float(item.get("shortAccount", 0.5))
                if short_pct > 0:
                    return round(long_pct / short_pct, 4)
    except Exception as e:
        log.debug(f"[BTCRegime] Binance L/S error: {e}")
    return 1.0


def _get_multi_tf_momentum() -> Tuple[float, float, float]:
    """
    Momentum BTC em 3 timeframes: 15m, 1h, 4h.
    Usa klines Min15 com limite de 20 (~5h de dados).

    Returns: (momentum_15m, momentum_1h, momentum_4h) em %
    """
    try:
        r = requests.get(
            f"{MEXC_FUTURES}/api/v1/contract/kline/BTC_USDT",
            params={"interval": "Min15", "limit": 20},
            headers=_HEADERS,
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        raw = data.get("data", data) if isinstance(data, dict) else data

        if isinstance(raw, dict) and "close" in raw and "open" in raw:
            closes = [float(c) for c in raw["close"]]
            opens  = [float(o) for o in raw["open"]]

            if len(closes) < 2:
                return 0.0, 0.0, 0.0

            price_now = closes[-1]

            # 15m: última vela
            price_15m = opens[-1] if len(opens) >= 1 else price_now
            m_15m = ((price_now - price_15m) / price_15m * 100) if price_15m > 0 else 0

            # 1h: 4 velas atrás
            price_1h = opens[-4] if len(opens) >= 4 else opens[0]
            m_1h = ((price_now - price_1h) / price_1h * 100) if price_1h > 0 else 0

            # 4h: 16 velas atrás
            price_4h = opens[-16] if len(opens) >= 16 else opens[0]
            m_4h = ((price_now - price_4h) / price_4h * 100) if price_4h > 0 else 0

            return round(m_15m, 2), round(m_1h, 2), round(m_4h, 2)

    except Exception as e:
        log.debug(f"[BTCRegime] Klines error: {e}")

    return 0.0, 0.0, 0.0


def _get_bull_market_support_band() -> dict:
    """
    Bull Market Support Band: 20-week SMA (140 dias) + 21-week EMA (147 dias).
    Preço acima de ambas = bull market confirmado.
    Preço abaixo = bear market.
    Cruzando = transição (TRANSITION).
    Cache 1h — não muda intraday.
    """
    now = time.time()
    if _bull_band_cache["data"] and (now - _bull_band_cache["ts"]) < BULL_BAND_CACHE_TTL:
        return _bull_band_cache["data"]

    result = _default_bull_band()

    try:
        r = requests.get(
            f"{MEXC_FUTURES}/api/v1/contract/kline/BTC_USDT",
            params={"interval": "Day1", "limit": 200},
            headers=_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        raw  = data.get("data", data) if isinstance(data, dict) else data

        closes = []
        if isinstance(raw, dict) and "close" in raw:
            closes = [float(c) for c in raw["close"] if c]
        elif isinstance(raw, list):
            closes = [float(k.get("c", k.get("close", 0))) for k in raw if isinstance(k, dict)]

        if len(closes) < 147:
            log.debug(f"[BTCRegime] Bull Band: insuficiente — {len(closes)} < 147 candles")
            _bull_band_cache.update({"data": result, "ts": now})
            return result

        price = closes[-1]
        if price <= 0:
            _bull_band_cache.update({"data": result, "ts": now})
            return result

        # 20-week SMA (140 dias)
        sma_20w = sum(closes[-140:]) / 140

        # 21-week EMA (147 dias) — seed SMA dos primeiros 147, depois EMA real
        ema_period = 147
        multiplier = 2.0 / (ema_period + 1)
        ema = sum(closes[:ema_period]) / ema_period
        for c in closes[ema_period:]:
            ema = (c - ema) * multiplier + ema
        ema_21w = ema

        band_mid     = (sma_20w + ema_21w) / 2
        distance_pct = ((price - band_mid) / band_mid * 100) if band_mid > 0 else 0

        if price > sma_20w and price > ema_21w:
            position = "ABOVE"
            bias     = "BULL"
        elif price < sma_20w and price < ema_21w:
            position = "BELOW"
            bias     = "BEAR"
        else:
            position = "CROSSING"
            bias     = "TRANSITION"

        result = {
            "sma_20w":        round(sma_20w, 2),
            "ema_21w":        round(ema_21w, 2),
            "price_vs_band":  position,
            "bull_band_bias": bias,
            "distance_pct":   round(distance_pct, 2),
        }

        log.info(
            f"[BTCRegime] Bull Band: SMA20w=${sma_20w:,.0f} EMA21w=${ema_21w:,.0f} "
            f"price=${price:,.0f} → {position} ({distance_pct:+.1f}%)"
        )

    except Exception as e:
        log.debug(f"[BTCRegime] Bull Band error: {e}")

    _bull_band_cache.update({"data": result, "ts": now})
    return result


def _default_bull_band() -> dict:
    return {
        "sma_20w": 0, "ema_21w": 0, "price_vs_band": "UNKNOWN",
        "bull_band_bias": "NEUTRAL", "distance_pct": 0,
    }


def _get_monthly_seasonality() -> dict:
    """
    Sazonalidade mensal BTC — média histórica 2015-2025.
    Não é regra absoluta — é contexto adicional (0-8 pts).

    Returns:
        {
            "month":       int,
            "multiplier":  float,
            "season":      str,   # STRONG / NEUTRAL / WEAK
            "description": str,
        }
    """
    from datetime import datetime, timezone
    month = datetime.now(timezone.utc).month
    mult  = _MONTHLY_SEASONALITY.get(month, 1.0)

    if mult >= 1.10:
        season = "STRONG"
        desc   = f"Mês {month} historicamente forte (×{mult:.2f})"
    elif mult <= 0.91:
        season = "WEAK"
        desc   = f"Mês {month} historicamente fraco (×{mult:.2f})"
    else:
        season = "NEUTRAL"
        desc   = f"Mês {month} neutro (×{mult:.2f})"

    return {"month": month, "multiplier": mult, "season": season, "description": desc}


def _get_fred_key() -> str:
    """Obtém FRED API key do config."""
    try:
        import sys
        from pathlib import Path
        _base = Path(__file__).parent.parent.parent
        if str(_base) not in sys.path:
            sys.path.insert(0, str(_base))
        from config import FRED_API_KEY
        return FRED_API_KEY or ""
    except Exception:
        import os
        return os.getenv("FRED_API_KEY", "")


def _fetch_fred_series(series_id: str, api_key: str, limit: int = 8) -> list:
    """
    Busca observações de uma série FRED.
    Retorna lista ordenada do mais recente ao mais antigo.
    """
    try:
        r = requests.get(
            FRED_BASE,
            params={
                "series_id":  series_id,
                "api_key":    api_key,
                "file_type":  "json",
                "limit":      limit,
                "sort_order": "desc",
            },
            headers=_HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            log.debug(f"[BTCRegime] FRED {series_id}: status {r.status_code}")
            return []
        data = r.json()
        observations = data.get("observations", [])
        # Filtrar valores válidos (FRED retorna "." para dados pendentes)
        return [obs for obs in observations if obs.get("value", ".") != "."]
    except Exception as e:
        log.debug(f"[BTCRegime] FRED {series_id} error: {e}")
        return []


def _default_macro() -> dict:
    return {
        "nfci_value": 0.0, "nfci_prev": 0.0,
        "nfci_direction": "STABLE", "nfci_regime": "NEUTRAL",
        "m2_yoy_pct": 0.0, "m2_direction": "STABLE",
        "macro_bias": "NEUTRAL", "macro_strength": 0,
    }


def _get_macro_conditions() -> dict:
    """
    Busca NFCI e M2 do FRED. Cache de 1 hora.

    NFCI (National Financial Conditions Index):
      Negativo = condições frouxas = RISK ON = bullish BTC
      Positivo = condições apertadas = RISK OFF = bearish BTC
      A DIREÇÃO (melhorando ou piorando) é tão importante quanto o nível.

    M2 (Money Supply):
      Crescendo = mais liquidez = bullish
      Contraindo = menos liquidez = bearish

    Returns:
        {
            "nfci_value":      float,  # valor atual (-0.433 = frouxo)
            "nfci_prev":       float,  # valor semana anterior
            "nfci_direction":  str,    # LOOSENING / TIGHTENING / STABLE
            "nfci_regime":     str,    # RISK_ON / RISK_OFF / NEUTRAL
            "m2_yoy_pct":      float,  # M2 variação % ano-sobre-ano
            "m2_direction":    str,    # EXPANDING / CONTRACTING / STABLE
            "macro_bias":      str,    # BULLISH / BEARISH / NEUTRAL
            "macro_strength":  float,  # 0-100
        }
    """
    now = time.time()
    if _macro_cache["data"] and (now - _macro_cache["ts"]) < MACRO_CACHE_TTL:
        return _macro_cache["data"]

    fred_key = _get_fred_key()
    if not fred_key:
        log.debug("[BTCRegime] FRED key não configurada")
        return _default_macro()

    result = _default_macro()

    try:
        # ── NFCI (semanal) ────────────────────────────────────────────────
        nfci_data = _fetch_fred_series("NFCI", fred_key, limit=8)
        if len(nfci_data) >= 2:
            nfci_current = float(nfci_data[0]["value"])
            nfci_prev    = float(nfci_data[1]["value"])
            nfci_4w_ago  = float(nfci_data[3]["value"]) if len(nfci_data) > 3 else nfci_prev

            delta_1w = nfci_current - nfci_prev
            delta_4w = nfci_current - nfci_4w_ago

            if delta_1w < -0.05 or delta_4w < -0.15:
                nfci_direction = "LOOSENING"
            elif delta_1w > 0.05 or delta_4w > 0.15:
                nfci_direction = "TIGHTENING"
            else:
                nfci_direction = "STABLE"

            if nfci_current < -0.10:
                nfci_regime = "RISK_ON"
            elif nfci_current > 0.10:
                nfci_regime = "RISK_OFF"
            else:
                nfci_regime = "NEUTRAL"

            result["nfci_value"]     = round(nfci_current, 4)
            result["nfci_prev"]      = round(nfci_prev, 4)
            result["nfci_direction"] = nfci_direction
            result["nfci_regime"]    = nfci_regime

            log.info(
                f"[BTCRegime] NFCI: {nfci_current:.3f} (prev: {nfci_prev:.3f}) "
                f"dir={nfci_direction} regime={nfci_regime}"
            )
    except Exception as e:
        log.debug(f"[BTCRegime] NFCI fetch error: {e}")

    try:
        # ── M2 Money Supply (semanal — WM2NS) ────────────────────────────
        m2_data = _fetch_fred_series("WM2NS", fred_key, limit=60)
        if len(m2_data) >= 52:
            m2_current  = float(m2_data[0]["value"])
            m2_year_ago = float(m2_data[51]["value"])

            m2_yoy = ((m2_current - m2_year_ago) / m2_year_ago * 100) if m2_year_ago > 0 else 0.0

            m2_4w_ago = float(m2_data[3]["value"]) if len(m2_data) > 3 else m2_current
            m2_trend  = m2_current - m2_4w_ago

            if m2_trend > 0 and m2_yoy > 2:
                m2_direction = "EXPANDING"
            elif m2_trend < 0 and m2_yoy < 0:
                m2_direction = "CONTRACTING"
            else:
                m2_direction = "STABLE"

            result["m2_yoy_pct"]   = round(m2_yoy, 2)
            result["m2_direction"] = m2_direction

            log.info(
                f"[BTCRegime] M2: YoY={m2_yoy:.1f}% dir={m2_direction} "
                f"current={m2_current:.0f}B prev_4w={m2_4w_ago:.0f}B"
            )
        elif len(m2_data) >= 2:
            m2_current = float(m2_data[0]["value"])
            m2_prev    = float(m2_data[1]["value"])
            result["m2_yoy_pct"]   = 0.0
            result["m2_direction"] = "EXPANDING" if m2_current > m2_prev else "CONTRACTING"
    except Exception as e:
        log.debug(f"[BTCRegime] M2 fetch error: {e}")

    # ── Macro bias combinado ──────────────────────────────────────────────
    bull_signals = 0
    bear_signals = 0

    if result["nfci_regime"] == "RISK_ON":
        bull_signals += 1
    elif result["nfci_regime"] == "RISK_OFF":
        bear_signals += 1

    if result["nfci_direction"] == "LOOSENING":
        bull_signals += 1
    elif result["nfci_direction"] == "TIGHTENING":
        bear_signals += 1

    if result["m2_direction"] == "EXPANDING":
        bull_signals += 1
    elif result["m2_direction"] == "CONTRACTING":
        bear_signals += 1

    if result["m2_yoy_pct"] > 5:
        bull_signals += 1
    elif result["m2_yoy_pct"] < -2:
        bear_signals += 1

    if bull_signals >= 3:
        result["macro_bias"]     = "BULLISH"
        result["macro_strength"] = min(100, bull_signals * 25)
    elif bear_signals >= 3:
        result["macro_bias"]     = "BEARISH"
        result["macro_strength"] = min(100, bear_signals * 25)
    elif bull_signals > bear_signals:
        result["macro_bias"]     = "BULLISH"
        result["macro_strength"] = min(100, bull_signals * 20)
    elif bear_signals > bull_signals:
        result["macro_bias"]     = "BEARISH"
        result["macro_strength"] = min(100, bear_signals * 20)
    else:
        result["macro_bias"]     = "NEUTRAL"
        result["macro_strength"] = 0

    _macro_cache.update({"data": result, "ts": now})
    return result


def _default_regime() -> dict:
    return {
        "direction": "NEUTRAL", "strength": 0, "bias": "NEUTRAL",
        "transition": "", "transition_boost": 1.0,
        "funding_rate": 0, "funding_ann": 0, "funding_accel": "STABLE",
        "price_change_pct": 0, "momentum_15m": 0, "momentum_1h": 0, "momentum_4h": 0,
        "oi_usd": 0, "ls_ratio": 1.0, "signals": [],
        "bull_score": 0, "bear_score": 0, "confirmations": 0, "momentum_score": 0,
        "nfci_value": 0, "nfci_regime": "NEUTRAL", "nfci_direction": "STABLE",
        "m2_yoy_pct": 0, "m2_direction": "STABLE",
        "macro_bias": "NEUTRAL", "macro_strength": 0,
        "bull_band_sma": 0, "bull_band_ema": 0,
        "bull_band_bias": "NEUTRAL", "bull_band_dist": 0,
        "seasonality_month": 0, "seasonality_mult": 1.0, "seasonality_season": "NEUTRAL",
    }
