"""
institutional_direction_engine.py — Orquestrador Principal
Trinity Trading | Institutional Direction Engine v1.0

Pipeline:
  1. Coleta de dados: Spot + Perp OHLCV, funding rate, OI, perp premium
  2. Market Maker Engine  (rejeições, zonas de defesa)       × 0.40
  3. Delta Engine         (imbalance, absorção, divergência)  × 0.35
  4. Perp/Spot Engine     (move real vs fake, funding)        × 0.25
  5. Direction Model      (score composto 0-100)
  6. Rare Setup Detection (4 setups institucionais raros)

Pesos:
  market_maker : 0.40
  delta        : 0.35
  perp_spot    : 0.25

Cache TTL: 300s (5 minutos) — order flow muda rápido

Fontes de dados (em ordem de prioridade):
  1. MEXC via ccxt (spot + swap/perp)
  2. yfinance fallback (apenas spot, sem derivativos)
"""

import time
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

try:
    import ccxt
    CCXT_OK = True
except ImportError:
    CCXT_OK = False

from .market_maker_engine import run_market_maker_engine
from .delta_engine         import run_delta_engine
from .perp_spot_engine     import run_perp_spot_engine

# ─── Configuração ─────────────────────────────────────────────────────────────

DIRECTION_WEIGHTS = {
    "market_maker": 0.40,
    "delta":        0.35,
    "perp_spot":    0.25,
}

DIRECTION_CACHE_TTL = 300   # 5 minutos

# Threshold de decisão
DIRECTION_UP_THRESHOLD   = 62
DIRECTION_DOWN_THRESHOLD = 38

# ─── Cache ────────────────────────────────────────────────────────────────────

_cache:    dict = {}
_cache_ts: dict = {}


def _cache_get(key: str):
    if key in _cache and (time.time() - _cache_ts.get(key, 0)) < DIRECTION_CACHE_TTL:
        return _cache[key]
    return None


def _cache_set(key: str, value):
    _cache[key] = value
    _cache_ts[key] = time.time()


# ─── Exchange (ccxt) ──────────────────────────────────────────────────────────

_exchange_instance   = None
_exchange_ts         = 0
EXCHANGE_REUSE_TTL   = 3600   # 1h


def _get_exchange() -> Optional[object]:
    """Retorna instância ccxt MEXC reutilizável (lazy + cached)."""
    global _exchange_instance, _exchange_ts
    if (
        _exchange_instance is not None
        and (time.time() - _exchange_ts) < EXCHANGE_REUSE_TTL
    ):
        return _exchange_instance

    if not CCXT_OK:
        return None

    try:
        exchange = ccxt.mexc({
            "apiKey":          os.getenv("MEXC_API_KEY",    ""),
            "secret":          os.getenv("MEXC_API_SECRET", ""),
            "enableRateLimit": True,
            "options":         {"defaultType": "spot"},
        })
        exchange.load_markets()
        _exchange_instance = exchange
        _exchange_ts       = time.time()
        return exchange
    except Exception as e:
        log.debug(f"   ccxt MEXC init falhou: {e}")
        return None


def _ohlcv_safe(exchange, symbol: str, timeframe: str, limit: int = 100) -> list:
    """Busca OHLCV com tratamento de erro."""
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return data or []
    except Exception as e:
        log.debug(f"   OHLCV {symbol} {timeframe} falhou: {e}")
        return []


def _fetch_funding_rate(exchange, perp_symbol: str) -> float:
    """Busca funding rate do mercado perpetual."""
    try:
        exchange.options["defaultType"] = "swap"
        result = exchange.fetch_funding_rate(perp_symbol)
        exchange.options["defaultType"] = "spot"
        return float(result.get("fundingRate", 0.0))
    except Exception:
        try:
            exchange.options["defaultType"] = "spot"
        except Exception:
            pass
        return 0.0


def _fetch_oi_change(exchange, perp_symbol: str) -> float:
    """Retorna variação percentual do OI nas últimas ~24h."""
    try:
        exchange.options["defaultType"] = "swap"
        history = exchange.fetch_open_interest_history(perp_symbol, timeframe="1h", limit=25)
        exchange.options["defaultType"] = "spot"

        if len(history) >= 2:
            oi_now  = float(history[-1].get("openInterestAmount", 0))
            oi_prev = float(history[0].get("openInterestAmount",  1))
            if oi_prev > 0:
                return round((oi_now - oi_prev) / oi_prev * 100, 2)
    except Exception:
        try:
            exchange.options["defaultType"] = "spot"
        except Exception:
            pass
    return 0.0


def _fetch_perp_premium(exchange, spot_price: float, perp_symbol: str) -> float:
    """(perp_price - spot_price) / spot_price."""
    try:
        exchange.options["defaultType"] = "swap"
        ticker = exchange.fetch_ticker(perp_symbol)
        exchange.options["defaultType"] = "spot"

        perp_price = float(ticker.get("last", spot_price))
        if spot_price > 0:
            return round((perp_price - spot_price) / spot_price, 6)
    except Exception:
        try:
            exchange.options["defaultType"] = "spot"
        except Exception:
            pass
    return 0.0


# ─── Coleta de dados ──────────────────────────────────────────────────────────

def _collect_data(symbol: str) -> dict:
    """
    Coleta todos os dados necessários: spot + perp OHLCV, derivatives.

    Returns dict com:
      ohlcv_spot_15m, ohlcv_spot_1h, ohlcv_spot_4h
      ohlcv_perp_1h
      funding_rate, oi_change_pct, perp_premium
      current_price
    """
    base        = symbol.split("/")[0]
    spot_symbol = f"{base}/USDT" if "/" not in symbol else symbol
    perp_symbol = f"{base}/USDT:USDT"

    result = {
        "ohlcv_spot_15m": [],
        "ohlcv_spot_1h":  [],
        "ohlcv_spot_4h":  [],
        "ohlcv_perp_1h":  [],
        "funding_rate":   0.0,
        "oi_change_pct":  0.0,
        "perp_premium":   0.0,
        "current_price":  0.0,
    }

    exchange = _get_exchange()

    # ── Fallback yfinance ─────────────────────────────────────────────────
    if exchange is None:
        try:
            import yfinance as yf
            import pandas as pd

            tk = yf.Ticker("BTC-USD")

            # 1H — últimas 2 semanas
            df1h = tk.history(period="14d", interval="1h").dropna()
            if not df1h.empty:
                ohlcv_1h = [
                    [int(ts.timestamp() * 1000),
                     float(r.Open), float(r.High), float(r.Low),
                     float(r.Close), float(r.Volume)]
                    for ts, r in df1h.iterrows()
                ]
                result["ohlcv_spot_1h"]  = ohlcv_1h
                result["ohlcv_spot_15m"] = ohlcv_1h  # proxy
                result["current_price"]  = float(df1h["Close"].iloc[-1])

            # 4H — resample de 1H
            if not df1h.empty:
                df4h = df1h.resample("4h").agg(
                    {"Open": "first", "High": "max", "Low": "min",
                     "Close": "last",  "Volume": "sum"}
                ).dropna()
                ohlcv_4h = [
                    [int(ts.timestamp() * 1000),
                     float(r.Open), float(r.High), float(r.Low),
                     float(r.Close), float(r.Volume)]
                    for ts, r in df4h.iterrows()
                ]
                result["ohlcv_spot_4h"] = ohlcv_4h

        except Exception as e:
            log.debug(f"   yfinance fallback falhou: {e}")
        return result

    # ── MEXC via ccxt ─────────────────────────────────────────────────────
    try:
        # Spot OHLCV
        result["ohlcv_spot_15m"] = _ohlcv_safe(exchange, spot_symbol, "15m", 100)
        result["ohlcv_spot_1h"]  = _ohlcv_safe(exchange, spot_symbol, "1h",  100)
        result["ohlcv_spot_4h"]  = _ohlcv_safe(exchange, spot_symbol, "4h",   60)

        # Preço atual
        if result["ohlcv_spot_1h"]:
            result["current_price"] = float(result["ohlcv_spot_1h"][-1][4])

        # Perp OHLCV
        exchange.options["defaultType"] = "swap"
        result["ohlcv_perp_1h"] = _ohlcv_safe(exchange, perp_symbol, "1h", 100)
        exchange.options["defaultType"] = "spot"

        # Derivatives
        result["funding_rate"]  = _fetch_funding_rate(exchange, perp_symbol)
        result["oi_change_pct"] = _fetch_oi_change(exchange, perp_symbol)
        result["perp_premium"]  = _fetch_perp_premium(
            exchange, result["current_price"], perp_symbol
        )

    except Exception as e:
        log.debug(f"   Coleta parcialmente falhou: {e}")

    return result


# ─── Direction Model ─────────────────────────────────────────────────────────

def _compute_direction(mm_score: float,
                       delta_score: float,
                       perp_spot_score: float) -> dict:
    """
    Combina os 3 scores com pesos e determina direção final.

    Score 0-100:
      ≥ 62 → UP
      ≤ 38 → DOWN
      38-62 → NEUTRAL
    """
    score = round(
        mm_score        * DIRECTION_WEIGHTS["market_maker"] +
        delta_score     * DIRECTION_WEIGHTS["delta"]        +
        perp_spot_score * DIRECTION_WEIGHTS["perp_spot"],
        1
    )
    score = max(0.0, min(100.0, score))

    if   score >= DIRECTION_UP_THRESHOLD:
        prediction = "UP"
        confidence = score
    elif score <= DIRECTION_DOWN_THRESHOLD:
        prediction = "DOWN"
        confidence = 100.0 - score
    else:
        prediction = "NEUTRAL"
        confidence = 40.0 + abs(score - 50.0) * 2

    return {
        "direction_score":       score,
        "direction_prediction":  prediction,
        "direction_confidence":  round(min(95.0, confidence), 1),
    }


def _compute_breakout_probability(direction_score: float,
                                   mm_data: dict,
                                   delta_data: dict,
                                   perp_spot_data: dict) -> float:
    """
    Estima probabilidade de breakout confirmado.
    Base: distância do score a 50 (neutralidade).
    """
    prob = abs(direction_score - 50.0) * 2.0   # 0-100 base

    # Absorção = energia acumulada = breakout iminente
    if delta_data.get("absorption_detected"):
        prob = min(95.0, prob + 12.0)

    # Move real no spot = breakout sustentável
    if perp_spot_data.get("move_validity") == "REAL":
        prob = min(95.0, prob + 8.0)

    # Fake move → desconta probabilidade de breakout real
    if perp_spot_data.get("fake_move_probability", 30) >= 65:
        prob = max(5.0, prob - 18.0)

    # Defesa MM forte = muita energia acumulada = breakout iminente
    if mm_data.get("market_maker_defense") and mm_data.get("defense_strength", 0) >= 70:
        prob = min(95.0, prob + 8.0)

    return round(min(95.0, max(5.0, prob)), 1)


def _majority_bias(mm_dir: str, delta_bias: str, perp_bias: str) -> str:
    """Voto majoritário entre os 3 engines."""
    votes = {
        "BULLISH": 0,
        "BEARISH": 0,
    }
    if mm_dir == "UP":       votes["BULLISH"] += 1
    elif mm_dir == "DOWN":   votes["BEARISH"] += 1
    if delta_bias == "BULLISH":  votes["BULLISH"] += 1
    elif delta_bias == "BEARISH": votes["BEARISH"] += 1
    if perp_bias == "UP":    votes["BULLISH"] += 1
    elif perp_bias == "DOWN": votes["BEARISH"] += 1

    if votes["BULLISH"] > votes["BEARISH"]:   return "BULLISH"
    if votes["BEARISH"] > votes["BULLISH"]:   return "BEARISH"
    return "NEUTRAL"


# ─── Rare Setup Detection ─────────────────────────────────────────────────────

# 4 setups institucionais raros
RARE_SETUPS = {

    "MM_DEFENSE_ABSORPTION_SPOT_STRENGTH": {
        "description": (
            "Market Maker defendendo nível-chave + Absorção de venda detectada "
            "+ Spot dominando volume — setup de acumulação antes de pump"
        ),
    },
    "SHORT_SQUEEZE_LOADING": {
        "description": (
            "Funding negativo extremo + Absorção de venda + MM em suporte "
            "— shorts armadilhados, squeeze iminente"
        ),
    },
    "INSTITUTIONAL_BREAKOUT_PREP": {
        "description": (
            "Spot acumulando + Absorção de compra (sem reação de preço) "
            "+ OI crescendo — institucional preparando breakout"
        ),
    },
    "FAKE_PUMP_REVERSAL": {
        "description": (
            "Perp dominando + Funding positivo extremo + Spot fraco "
            "— pump alavancado, reversão de alta probabilidade"
        ),
    },
}


def _detect_rare_setup(mm: dict, delta: dict, ps: dict,
                        direction_score: float) -> dict:
    """
    Avalia os 4 setups raros e retorna o primeiro que se qualificar.
    Retorna neutro se nenhum atingir o threshold mínimo de força.
    """
    _no_setup = {
        "institutional_rare_setup": False,
        "setup_type":               None,
        "setup_strength":           0.0,
        "setup_description":        None,
    }

    mm_defense   = mm.get("market_maker_defense", False)
    mm_strength  = mm.get("defense_strength", 0.0)
    mm_type      = mm.get("defense_type", None)
    mm_dir       = mm.get("likely_direction", "NEUTRAL")

    abs_detected = delta.get("absorption_detected", False)
    abs_type     = delta.get("absorption_type", None)
    delta_conf   = delta.get("confidence", 50.0)
    div_type     = delta.get("divergence_type", None)

    dominant     = ps.get("dominant_market", "BALANCED")
    validity     = ps.get("move_validity", "WEAK")
    f_bias       = ps.get("funding_bias", "NEUTRAL")
    f_extreme    = ps.get("funding_extreme", False)
    spot_trend   = ps.get("spot_vol_trend", "STABLE")
    fake_prob    = ps.get("fake_move_probability", 30.0)
    oi_change    = ps.get("oi_change_pct", 0.0)

    # ── Setup 1: MM Defense + Absorption + Spot Strength ─────────────────
    if (
        mm_defense and mm_strength >= 55 and
        abs_type == "SELL_ABSORPTION" and
        dominant in ("SPOT", "BALANCED") and
        spot_trend in ("EXPANDING", "STABLE")
    ):
        strength = round(
            mm_strength * 0.40 + delta_conf * 0.40 + (100 - fake_prob) * 0.20,
            1
        )
        if strength >= 65:
            return {
                "institutional_rare_setup": True,
                "setup_type":               "MM_DEFENSE_ABSORPTION_SPOT_STRENGTH",
                "setup_strength":           min(100.0, strength),
                "setup_description":        RARE_SETUPS["MM_DEFENSE_ABSORPTION_SPOT_STRENGTH"]["description"],
            }

    # ── Setup 2: Short Squeeze Loading ────────────────────────────────────
    if (
        f_extreme and f_bias in ("BULLISH",) and
        abs_type == "SELL_ABSORPTION" and
        mm_type == "SUPPORT" and mm_dir == "UP"
    ):
        strength = round(mm_strength * 0.35 + delta_conf * 0.40 + 25.0, 1)
        if strength >= 65:
            return {
                "institutional_rare_setup": True,
                "setup_type":               "SHORT_SQUEEZE_LOADING",
                "setup_strength":           min(100.0, strength),
                "setup_description":        RARE_SETUPS["SHORT_SQUEEZE_LOADING"]["description"],
            }

    # ── Setup 3: Institutional Breakout Prep ──────────────────────────────
    if (
        spot_trend == "EXPANDING" and
        abs_type == "BUY_ABSORPTION" and
        oi_change >= 4.0 and
        direction_score >= 55
    ):
        strength = round(direction_score * 0.50 + delta_conf * 0.30 + 18.0, 1)
        if strength >= 65:
            return {
                "institutional_rare_setup": True,
                "setup_type":               "INSTITUTIONAL_BREAKOUT_PREP",
                "setup_strength":           min(100.0, strength),
                "setup_description":        RARE_SETUPS["INSTITUTIONAL_BREAKOUT_PREP"]["description"],
            }

    # ── Setup 4: Fake Pump Reversal ───────────────────────────────────────
    if (
        dominant == "PERP" and
        f_extreme and f_bias in ("BEARISH",) and
        validity == "FAKE" and
        spot_trend in ("STABLE", "CONTRACTING")
    ):
        strength = round(fake_prob * 0.55 + mm_strength * 0.20 + 22.0, 1)
        if strength >= 65:
            return {
                "institutional_rare_setup": True,
                "setup_type":               "FAKE_PUMP_REVERSAL",
                "setup_strength":           min(100.0, strength),
                "setup_description":        RARE_SETUPS["FAKE_PUMP_REVERSAL"]["description"],
            }

    return _no_setup


# ─── Neutral Output ───────────────────────────────────────────────────────────

def _neutral_output() -> dict:
    return {
        "direction_prediction":     "NEUTRAL",
        "direction_confidence":     50.0,
        "direction_score":          50.0,
        "institutional_bias":       "NEUTRAL",
        "fake_move_probability":    30.0,
        "breakout_probability":     30.0,
        "institutional_rare_setup": False,
        "setup_type":               None,
        "setup_strength":           0.0,
        "setup_description":        None,
        "market_maker": {
            "defense":           False,
            "defense_level":     None,
            "defense_type":      None,
            "defense_strength":  0.0,
            "likely_direction":  "NEUTRAL",
            "mm_score":          50.0,
            "rejections":        0,
            "all_levels":        [],
        },
        "delta": {
            "imbalance":           False,
            "delta_type":          "BALANCED",
            "absorption_detected": False,
            "absorption_type":     None,
            "absorption_strength": 0.0,
            "divergence_detected": False,
            "divergence_type":     None,
            "exhaustion_detected": False,
            "exhaustion_type":     None,
            "institutional_bias":  "NEUTRAL",
            "confidence":          50.0,
            "delta_score":         50.0,
            "net_delta_ratio":     0.0,
        },
        "perp_spot": {
            "divergence":         False,
            "dominant_market":    "BALANCED",
            "move_validity":      "WEAK",
            "directional_bias":   "NEUTRAL",
            "fake_probability":   30.0,
            "funding_bias":       "NEUTRAL",
            "funding_extreme":    False,
            "funding_rate":       0.0,
            "funding_annual_pct": 0.0,
            "spot_vol_trend":     "STABLE",
            "perp_vol_trend":     "STABLE",
            "oi_change_pct":      0.0,
            "perp_premium":       0.0,
            "perp_spot_score":    50.0,
            "signals":            [],
        },
        "current_price":  0.0,
        "funding_rate":   0.0,
        "oi_change_pct":  0.0,
    }


# ─── Engine Principal ─────────────────────────────────────────────────────────

class InstitutionalDirectionEngine:
    """
    Orquestrador do pipeline completo de detecção de direção institucional.

    Uso:
        engine = InstitutionalDirectionEngine()
        result = engine.run("BTC/USDT")
    """

    def run(self, symbol: str) -> dict:
        cache_key = f"ide_{symbol}"
        cached    = _cache_get(cache_key)
        if cached is not None:
            log.debug("   Institutional Direction Engine: cache hit")
            return cached

        log.info(f"   Institutional Direction Engine: analisando {symbol}...")

        try:
            # ── 1. Coleta de dados ────────────────────────────────────────
            data = _collect_data(symbol)

            if not data["ohlcv_spot_1h"]:
                log.warning("   IDE: sem dados OHLCV spot — retornando neutro")
                return _neutral_output()

            price         = data["current_price"]
            funding_rate  = data["funding_rate"]
            oi_change_pct = data["oi_change_pct"]
            perp_premium  = data["perp_premium"]

            # OI bias para auxiliar o MM engine
            if   oi_change_pct > 5:  oi_bias = "BULLISH"
            elif oi_change_pct < -5: oi_bias = "BEARISH"
            else:                    oi_bias = "NEUTRAL"

            # ── 2. Market Maker Engine ────────────────────────────────────
            log.debug("   [IDE-1] Market Maker Engine...")
            mm_data = run_market_maker_engine(
                ohlcv_1h      = data["ohlcv_spot_1h"],
                ohlcv_4h      = data["ohlcv_spot_4h"] or data["ohlcv_spot_1h"],
                current_price = price,
                funding_rate  = funding_rate,
                oi_bias       = oi_bias,
            )

            # ── 3. Delta Engine ───────────────────────────────────────────
            log.debug("   [IDE-2] Delta Imbalance Engine...")
            delta_data = run_delta_engine(
                ohlcv_15m = data["ohlcv_spot_15m"] or data["ohlcv_spot_1h"],
                ohlcv_1h  = data["ohlcv_spot_1h"],
            )

            # ── 4. Perp/Spot Engine ───────────────────────────────────────
            log.debug("   [IDE-3] Perp/Spot Divergence Engine...")
            perp_spot_data = run_perp_spot_engine(
                ohlcv_spot_1h = data["ohlcv_spot_1h"],
                ohlcv_perp_1h = data["ohlcv_perp_1h"],
                funding_rate  = funding_rate,
                oi_change_pct = oi_change_pct,
                perp_premium  = perp_premium,
            )

            # ── 5. Direction Model ────────────────────────────────────────
            dir_result = _compute_direction(
                mm_score        = mm_data["mm_score"],
                delta_score     = delta_data["delta_score"],
                perp_spot_score = perp_spot_data["perp_spot_score"],
            )
            direction_score = dir_result["direction_score"]

            fake_prob     = perp_spot_data.get("fake_move_probability", 30.0)
            breakout_prob = _compute_breakout_probability(
                direction_score, mm_data, delta_data, perp_spot_data
            )

            inst_bias = _majority_bias(
                mm_data["likely_direction"],
                delta_data["institutional_bias"],
                perp_spot_data["directional_bias"],
            )

            # ── 6. Rare Setup Detection ───────────────────────────────────
            rare = _detect_rare_setup(mm_data, delta_data, perp_spot_data, direction_score)

            # ── Output Final ──────────────────────────────────────────────
            result = {
                "direction_prediction":     dir_result["direction_prediction"],
                "direction_confidence":     dir_result["direction_confidence"],
                "direction_score":          direction_score,
                "institutional_bias":       inst_bias,
                "fake_move_probability":    fake_prob,
                "breakout_probability":     breakout_prob,

                "institutional_rare_setup": rare["institutional_rare_setup"],
                "setup_type":               rare["setup_type"],
                "setup_strength":           rare["setup_strength"],
                "setup_description":        rare["setup_description"],

                "market_maker": {
                    "defense":            mm_data["market_maker_defense"],
                    "defense_level":      mm_data["defense_level"],
                    "defense_type":       mm_data["defense_type"],
                    "defense_strength":   mm_data["defense_strength"],
                    "likely_direction":   mm_data["likely_direction"],
                    "mm_score":           mm_data["mm_score"],
                    "rejections":         mm_data["rejections_found"],
                    "defense_timeframe":  mm_data.get("defense_timeframe"),
                    "all_levels":         mm_data.get("all_levels", []),
                },
                "delta": {
                    "imbalance":           delta_data["delta_imbalance"],
                    "delta_type":          delta_data["delta_type"],
                    "absorption_detected": delta_data["absorption_detected"],
                    "absorption_type":     delta_data["absorption_type"],
                    "absorption_strength": delta_data["absorption_strength"],
                    "divergence_detected": delta_data["divergence_detected"],
                    "divergence_type":     delta_data.get("divergence_type"),
                    "divergence_strength": delta_data.get("divergence_strength", 0.0),
                    "exhaustion_detected": delta_data["exhaustion_detected"],
                    "exhaustion_type":     delta_data["exhaustion_type"],
                    "institutional_bias":  delta_data["institutional_bias"],
                    "confidence":          delta_data["confidence"],
                    "delta_score":         delta_data["delta_score"],
                    "net_delta_ratio":     delta_data["net_delta_ratio"],
                    "buy_ratio":           delta_data.get("buy_ratio", 0.5),
                },
                "perp_spot": {
                    "divergence":         perp_spot_data["perp_vs_spot_divergence"],
                    "dominant_market":    perp_spot_data["dominant_market"],
                    "move_validity":      perp_spot_data["move_validity"],
                    "directional_bias":   perp_spot_data["directional_bias"],
                    "fake_probability":   perp_spot_data["fake_move_probability"],
                    "funding_bias":       perp_spot_data["funding_bias"],
                    "funding_extreme":    perp_spot_data["funding_extreme"],
                    "funding_rate":       perp_spot_data["funding_rate"],
                    "funding_annual_pct": perp_spot_data["funding_annual_pct"],
                    "spot_vol_trend":     perp_spot_data["spot_vol_trend"],
                    "perp_vol_trend":     perp_spot_data["perp_vol_trend"],
                    "oi_change_pct":      perp_spot_data["oi_change_pct"],
                    "perp_premium":       perp_spot_data["perp_premium"],
                    "perp_spot_score":    perp_spot_data["perp_spot_score"],
                    "signals":            perp_spot_data["signals"],
                },

                "current_price":  price,
                "funding_rate":   funding_rate,
                "oi_change_pct":  oi_change_pct,
            }

            _cache_set(cache_key, result)

            log.info(
                f"   IDE: {dir_result['direction_prediction']} "
                f"({dir_result['direction_confidence']:.0f}%) | "
                f"score={direction_score:.1f} | bias={inst_bias} | "
                f"fake={fake_prob:.0f}% | breakout={breakout_prob:.0f}% | "
                f"rare={rare['institutional_rare_setup']}"
            )
            return result

        except Exception as e:
            log.error(f"   Institutional Direction Engine falhou: {e}", exc_info=True)
            return _neutral_output()


# ─── Singleton entry point ────────────────────────────────────────────────────

_engine_singleton = InstitutionalDirectionEngine()


def run_institutional_direction_analysis(symbol: str = None) -> dict:
    """Entry point principal. Usa SYMBOL do env como fallback."""
    if symbol is None:
        symbol = os.getenv("SYMBOL", "BTC/USDT")
    return _engine_singleton.run(symbol)
