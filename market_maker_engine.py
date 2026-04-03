"""
market_maker_engine.py — Market Maker / Institutional Engine v2
SMC refinado: liquidez clusters, sweep wick, premium/discount Fibonacci,
MTF ponderado, trap detection, score institucional e confidence score.
Todas operações vetorizadas — otimizado para uso em tempo real.
"""
import logging
from typing import List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ─── Pesos MTF (total = 7.5) ──────────────────────────────────────────────────
MTF_WEIGHTS: dict = {"1D": 3.0, "4H": 2.0, "1H": 1.5, "15m": 1.0}
MTF_CONFIG: dict  = {
    "1D":  {"tf": "1d",  "limit": 200},
    "4H":  {"tf": "4h",  "limit": 200},
    "1H":  {"tf": "1h",  "limit": 200},
    "15m": {"tf": "15m", "limit": 300},
}

# ─── Pesos Score Institucional (soma = 100) ───────────────────────────────────
INST_WEIGHTS: dict = {
    "market_structure": 20,
    "liquidity":        18,
    "sweep":            17,
    "volume":           15,
    "trend":            13,
    "correlation":       9,
    "volatility":        8,
}


# ─────────────────────────────────────────────────────────────────────────────

class MarketMakerEngine:
    """
    Engine institucional SMC para futuros BTC/USDT.
    Detecta liquidez, sweeps, premium/discount, traps e bias MTF.
    """

    def __init__(self, df: pd.DataFrame, symbol: str = "BTC"):
        self.df      = df.copy()
        self.symbol  = symbol
        self._price  = float(df["close"].iloc[-1])
        self._high   = df["high"]
        self._low    = df["low"]
        self._close  = df["close"]
        self._open   = df["open"]
        self._volume = df["volume"]
        # Pré-computa swings vetorizados (uma vez por instância)
        self._sh      = self._compute_swing_highs(lookback=5)
        self._sl      = self._compute_swing_lows(lookback=5)
        self._atr_val = float(self._atr(14).iloc[-1]) or (self._price * 0.005)

    # =========================================================================
    # 1. LIQUIDITY POOL DETECTION
    # =========================================================================
    def detect_liquidity_pools(self) -> dict:
        """
        - Equal Highs/Lows com tolerância 0.05% / 0.10% / 0.20%
        - Clusters de liquidez por densidade de swings
        - Stop Hunt Zones = nível ± ATR * 0.3
        """
        sh = self._sh
        sl = self._sl

        # ── Equal Highs/Lows agrupados por tolerância ────────────────────────
        def _group_equals(prices: pd.Series, tol: float) -> List[float]:
            if len(prices) < 2:
                return []
            vals  = prices.values.astype(float)
            used  = np.zeros(len(vals), dtype=bool)
            out   = []
            for i in range(len(vals)):
                if used[i]:
                    continue
                mask   = (~used) & (np.abs(vals - vals[i]) / vals[i] <= tol)
                mask[i] = True
                if mask.sum() >= 2:
                    out.append(round(float(vals[mask].mean()), 2))
                used[mask] = True
            return out

        eq_highs_tight  = _group_equals(sh, 0.0005)   # 0.05%
        eq_highs_normal = _group_equals(sh, 0.0010)   # 0.10%
        eq_highs_wide   = _group_equals(sh, 0.0020)   # 0.20%
        eq_lows_tight   = _group_equals(sl, 0.0005)
        eq_lows_normal  = _group_equals(sl, 0.0010)
        eq_lows_wide    = _group_equals(sl, 0.0020)

        # Alvo de liquidez mais próximo acima/abaixo
        highs_above = sorted([h for h in eq_highs_normal if h > self._price])
        lows_below  = sorted([l for l in eq_lows_normal  if l < self._price], reverse=True)
        liq_target_high = (highs_above[0] if highs_above
                           else round(float(sh.max()), 2) if len(sh) > 0
                           else round(self._price * 1.01, 2))
        liq_target_low  = (lows_below[0] if lows_below
                           else round(float(sl.min()), 2) if len(sl) > 0
                           else round(self._price * 0.99, 2))

        # ── Clusters de densidade (histograma) ───────────────────────────────
        def _density_clusters(prices: pd.Series, bins: int = 20) -> List[dict]:
            if len(prices) < 5:
                return []
            arr  = prices.values.astype(float)
            hist, edges = np.histogram(arr, bins=bins)
            clusters = []
            for i in range(len(hist)):
                if hist[i] >= 2:
                    mid = float((edges[i] + edges[i + 1]) / 2)
                    clusters.append({
                        "price":    round(mid, 2),
                        "count":    int(hist[i]),
                        "strength": min(100, int(hist[i] * 25)),
                    })
            return sorted(clusters, key=lambda x: x["strength"], reverse=True)[:5]

        # ── Stop Hunt Zones ──────────────────────────────────────────────────
        stop_hunt_zones: List[dict] = []
        for lvl in eq_lows_normal[:3]:
            stop_hunt_zones.append({
                "type":         "bear_hunt",
                "zone_top":     round(lvl, 2),
                "zone_bottom":  round(lvl - self._atr_val * 0.3, 2),
                "distance_pct": round((self._price - lvl) / self._price * 100, 2),
            })
        for lvl in eq_highs_normal[:3]:
            stop_hunt_zones.append({
                "type":         "bull_hunt",
                "zone_bottom":  round(lvl, 2),
                "zone_top":     round(lvl + self._atr_val * 0.3, 2),
                "distance_pct": round((lvl - self._price) / self._price * 100, 2),
            })

        # ── Score ────────────────────────────────────────────────────────────
        score = 50
        if eq_lows_tight:           score += 15
        elif eq_lows_normal:        score += 8
        if eq_highs_tight:          score -= 15
        elif eq_highs_normal:       score -= 8
        # Proximidade do alvo < 2% → pressão de liquidez iminente
        if (self._price - liq_target_low) / self._price < 0.02:   score += 12
        if (liq_target_high - self._price) / self._price < 0.02:  score -= 12
        score = max(0, min(100, score))

        return {
            "equal_highs":      eq_highs_normal,
            "equal_lows":       eq_lows_normal,
            "eq_highs_tight":   eq_highs_tight,
            "eq_lows_tight":    eq_lows_tight,
            "eq_highs_wide":    eq_highs_wide,
            "eq_lows_wide":     eq_lows_wide,
            "liq_target_high":  liq_target_high,
            "liq_target_low":   liq_target_low,
            "clusters_high":    _density_clusters(sh),
            "clusters_low":     _density_clusters(sl),
            "stop_hunt_zones":  stop_hunt_zones,
            "score":            score,
            "bias":             "BULLISH" if score > 55 else "BEARISH" if score < 45 else "NEUTRO",
        }

    # =========================================================================
    # 2. SWEEP DETECTION (REFINADO)
    # =========================================================================
    def detect_sweeps(self) -> dict:
        """
        - Wick-based sweeps: wick ultrapassa swing, fecha dentro
        - Fake breakout/breakdown: fecha além e retorna
        - Rejection strength: tamanho do wick / range do candle
        - sweep_strength score 0-100
        """
        df    = self.df
        close = self._close
        high  = self._high
        low   = self._low
        vol   = self._volume
        sh    = self._sh
        sl    = self._sl

        avg_vol   = float(vol.rolling(20).mean().iloc[-1]) or float(vol.mean())
        rec_vol   = float(vol.iloc[-3:].mean())
        vol_ratio = rec_vol / avg_vol if avg_vol > 0 else 1.0

        sweep_low_wick     = False
        sweep_high_wick    = False
        fake_breakdown     = False   # fecha abaixo swing low
        fake_breakout      = False   # fecha acima swing high
        wick_rej_bull      = 0.0
        wick_rej_bear      = 0.0

        last_n = min(5, len(df))
        for i in range(-last_n, 0):
            c   = df.iloc[i]
            c_h = float(c["high"])
            c_l = float(c["low"])
            c_c = float(c["close"])
            c_o = float(c["open"])
            body     = abs(c_c - c_o) + 1e-9
            wick_dn  = min(c_o, c_c) - c_l   # wick inferior
            wick_up  = c_h - max(c_o, c_c)   # wick superior

            # ── Sweep de swing low ───────────────────────────────────────────
            if len(sl) >= 1:
                ref = float(sl.iloc[-1])
                if c_l < ref:
                    if c_c > ref:   # fechou acima = wick sweep (bullish)
                        sweep_low_wick = True
                        wick_rej_bull  = max(wick_rej_bull,
                            min(100.0, wick_dn / (body + wick_dn) * 100))
                    else:           # fechou abaixo = fake breakdown
                        fake_breakdown = True

            # ── Sweep de swing high ──────────────────────────────────────────
            if len(sh) >= 1:
                ref = float(sh.iloc[-1])
                if c_h > ref:
                    if c_c < ref:   # fechou abaixo = wick sweep (bearish)
                        sweep_high_wick = True
                        wick_rej_bear   = max(wick_rej_bear,
                            min(100.0, wick_up / (body + wick_up) * 100))
                    else:           # fechou acima = fake breakout
                        fake_breakout = True

        # ── sweep_strength (0-100, agnóstico de direção) ─────────────────────
        raw = 0
        if sweep_low_wick:
            raw += 30 + min(30, int(wick_rej_bull * 0.5))
            if vol_ratio > 1.3: raw += 15
        if sweep_high_wick:
            raw -= 30 + min(30, int(wick_rej_bear * 0.5))
            if vol_ratio > 1.3: raw -= 15

        score         = max(0, min(100, 50 + raw))
        sweep_strength = min(100, abs(raw) * 2)

        return {
            "sweep_low_wick":      sweep_low_wick,
            "sweep_high_wick":     sweep_high_wick,
            "fake_breakdown":      fake_breakdown,
            "fake_breakout":       fake_breakout,
            "wick_rejection_bull": round(wick_rej_bull, 1),
            "wick_rejection_bear": round(wick_rej_bear, 1),
            "vol_ratio":           round(vol_ratio, 2),
            "sweep_strength":      int(sweep_strength),
            "sweep_bias": (
                "BULLISH" if sweep_low_wick and not sweep_high_wick else
                "BEARISH" if sweep_high_wick and not sweep_low_wick else
                "NEUTRO"
            ),
            "score": score,
            "bias":  "BULLISH" if score > 55 else "BEARISH" if score < 45 else "NEUTRO",
        }

    # =========================================================================
    # 3. PREMIUM / DISCOUNT / EQUILIBRIUM  (Fibonacci)
    # =========================================================================
    def detect_premium_discount(self) -> dict:
        """
        Range = recent_high – recent_low (últimas 50 velas + últimos swings).
        Fibonacci:
          Discount  ≤ 38.2%
          Equilibrium  38.2% – 61.8%
          Premium   ≥ 61.8%
        """
        lookback    = min(50, len(self.df))
        recent_high = float(self._high.tail(lookback).max())
        recent_low  = float(self._low.tail(lookback).min())
        if len(self._sh) >= 1: recent_high = max(recent_high, float(self._sh.iloc[-1]))
        if len(self._sl) >= 1: recent_low  = min(recent_low,  float(self._sl.iloc[-1]))

        rng = recent_high - recent_low
        if rng <= 0:
            fallback = {
                "premium_zone": False, "discount_zone": False, "equilibrium_zone": True,
                "range_position_pct": 50.0,
                "recent_high": recent_high, "recent_low": recent_low,
                "fib_236": self._price, "fib_382": self._price,
                "fib_500": self._price, "fib_618": self._price,
                "fib_786": self._price, "equilibrium": self._price,
                "premium_level": self._price, "discount_level": self._price,
                "score": 50, "bias": "NEUTRO",
            }
            return fallback

        f236 = round(recent_low + rng * 0.236, 2)
        f382 = round(recent_low + rng * 0.382, 2)
        f500 = round(recent_low + rng * 0.500, 2)
        f618 = round(recent_low + rng * 0.618, 2)
        f786 = round(recent_low + rng * 0.786, 2)

        pos_pct       = (self._price - recent_low) / rng * 100
        premium_zone  = self._price >= f618
        discount_zone = self._price <= f382
        eq_zone       = f382 < self._price < f618

        score = 50
        if discount_zone:   score += 25   # barato = bullish
        elif premium_zone:  score -= 25   # caro = bearish
        if self._price <= f236: score += 10
        if self._price >= f786: score -= 10
        score = max(0, min(100, score))

        return {
            "premium_zone":       premium_zone,
            "discount_zone":      discount_zone,
            "equilibrium_zone":   eq_zone,
            "range_position_pct": round(pos_pct, 1),
            "recent_high":        round(recent_high, 2),
            "recent_low":         round(recent_low, 2),
            "equilibrium":        f500,
            "premium_level":      f618,
            "discount_level":     f382,
            "fib_236":            f236,
            "fib_382":            f382,
            "fib_500":            f500,
            "fib_618":            f618,
            "fib_786":            f786,
            "score":              score,
            "bias":               "BULLISH" if score > 55 else "BEARISH" if score < 45 else "NEUTRO",
        }

    # =========================================================================
    # 4. MTF INSTITUTIONAL BIAS  (1D=3 / 4H=2 / 1H=1.5 / 15m=1)
    # =========================================================================
    def multi_timeframe_bias(self) -> dict:
        """
        Bias institucional ponderado por relevância de TF.
        """
        from mexc_client import get_ohlcv

        results: dict = {}
        total_w = sum(MTF_WEIGHTS.values())   # 7.5

        for label, cfg in MTF_CONFIG.items():
            try:
                sym   = self.symbol if ":" in self.symbol else self.symbol + "/USDT:USDT"
                df_tf = get_ohlcv(sym, cfg["tf"], limit=cfg["limit"])
                eng   = MarketMakerEngine(df_tf, self.symbol)
                ms    = eng._fast_market_structure()
                pd_   = eng.detect_premium_discount()
                sw    = eng.detect_sweeps()
                tf_score = round(ms["score"] * 0.50 + pd_["score"] * 0.30 + sw["score"] * 0.20, 1)

                results[label] = {
                    "structure":  ms["structure"],
                    "bias":       ms["bias"],
                    "bos_bull":   ms["bos_bull"],
                    "bos_bear":   ms["bos_bear"],
                    "choch":      ms["choch"],
                    "premium":    pd_["premium_zone"],
                    "discount":   pd_["discount_zone"],
                    "sweep_bull": sw["sweep_low_wick"],
                    "sweep_bear": sw["sweep_high_wick"],
                    "score":      tf_score,
                    "weight":     MTF_WEIGHTS[label],
                }
                log.info(f"   MTF {label} (w={MTF_WEIGHTS[label]}): {ms['structure']} | score={tf_score}")
            except Exception as e:
                log.warning(f"   MTF {label} falhou: {e}")
                results[label] = {
                    "structure": "?", "bias": "NEUTRO", "score": 50.0,
                    "weight": MTF_WEIGHTS[label], "bos_bull": False,
                    "bos_bear": False, "choch": False,
                    "premium": False, "discount": False,
                    "sweep_bull": False, "sweep_bear": False,
                }

        # Bias ponderado
        weighted_score = sum(
            results[tf]["score"] * MTF_WEIGHTS[tf]
            for tf in MTF_WEIGHTS if tf in results
        ) / total_w

        bull_w = sum(MTF_WEIGHTS[tf] for tf in results if results[tf]["bias"] == "BULLISH")
        bear_w = sum(MTF_WEIGHTS[tf] for tf in results if results[tf]["bias"] == "BEARISH")

        if   bull_w >= 5.0:          inst_bias = "STRONG_BULL"
        elif bull_w >= 3.5:          inst_bias = "BULLISH"
        elif bear_w >= 5.0:          inst_bias = "STRONG_BEAR"
        elif bear_w >= 3.5:          inst_bias = "BEARISH"
        elif bull_w > bear_w:        inst_bias = "WEAK_BULL"
        elif bear_w > bull_w:        inst_bias = "WEAK_BEAR"
        else:                        inst_bias = "NEUTRAL"

        return {
            "institutional_bias": inst_bias,
            "weighted_score":     round(weighted_score, 1),
            "bull_weight":        round(bull_w, 1),
            "bear_weight":        round(bear_w, 1),
            "alignment_pct":      round(max(bull_w, bear_w) / total_w * 100, 1),
            "details":            results,
            "structure_map":      {tf: results[tf]["structure"] for tf in results},
        }

    # =========================================================================
    # 5. TRAP DETECTION
    # =========================================================================
    def detect_traps(self) -> dict:
        """
        - Fake breakout / breakdown (candle fecha além e reverte)
        - Liquidity inducement (3 swings dentro de 0.3%)
        - Low volume breakout (volume < 70% da média)
        - Divergência RSI em extremo
        Retorna trap_probability (0-100).
        """
        df    = self.df
        close = self._close
        high  = self._high
        low   = self._low
        vol   = self._volume
        sh    = self._sh
        sl    = self._sl

        avg_vol = float(vol.rolling(20).mean().iloc[-1]) or float(vol.mean())
        trap_score = 0
        signals: List[str] = []

        # ── Fake breakout (ultrapassa swing high e reverte) ──────────────────
        fake_breakout = False
        if len(sh) >= 1:
            ref = float(sh.iloc[-1])
            for i in range(-min(4, len(df)), 0):
                if float(high.iloc[i]) > ref and float(close.iloc[i]) < ref:
                    fake_breakout = True
                    trap_score   += 30
                    signals.append(f"Fake breakout ${ref:,.0f}")
                    break

        # ── Fake breakdown (ultrapassa swing low e reverte) ──────────────────
        fake_breakdown = False
        if len(sl) >= 1:
            ref = float(sl.iloc[-1])
            for i in range(-min(4, len(df)), 0):
                if float(low.iloc[i]) < ref and float(close.iloc[i]) > ref:
                    fake_breakdown = True
                    trap_score    += 30
                    signals.append(f"Fake breakdown ${ref:,.0f}")
                    break

        # ── Liquidity Inducement (3 swings em zone < 0.3%) ──────────────────
        eq_high_induce = False
        eq_low_induce  = False
        if len(sh) >= 3:
            last3 = sh.iloc[-3:]
            if (last3.max() - last3.min()) / last3.min() < 0.003:
                eq_high_induce = True
                trap_score    += 20
                signals.append(f"Inducement acima ${float(last3.mean()):,.0f}")
        if len(sl) >= 3:
            last3 = sl.iloc[-3:]
            if (last3.max() - last3.min()) / last3.min() < 0.003:
                eq_low_induce = True
                trap_score   += 20
                signals.append(f"Inducement abaixo ${float(last3.mean()):,.0f}")

        # ── Low Volume Breakout ───────────────────────────────────────────────
        low_vol_bo = False
        rec_vol    = float(vol.iloc[-1])
        if rec_vol < avg_vol * 0.70:
            broke_high = len(sh) >= 1 and float(close.iloc[-1]) > float(sh.iloc[-1])
            broke_low  = len(sl) >= 1 and float(close.iloc[-1]) < float(sl.iloc[-1])
            if broke_high or broke_low:
                low_vol_bo  = True
                trap_score += 25
                signals.append(f"Rompimento baixo volume ({rec_vol/avg_vol:.1f}x média)")

        # ── Divergência RSI ──────────────────────────────────────────────────
        rsi         = self._rsi(14)
        rsi_div_bear = False
        rsi_div_bull = False

        if len(sh) >= 2 and len(rsi) > 0:
            try:
                i1 = df.index.get_loc(sh.index[-1])
                i2 = df.index.get_loc(sh.index[-2])
                if float(sh.iloc[-1]) > float(sh.iloc[-2]) and float(rsi.iloc[i1]) < float(rsi.iloc[i2]):
                    rsi_div_bear = True
                    trap_score  += 15
                    signals.append("Divergência bearish RSI")
            except Exception:
                pass

        if len(sl) >= 2 and len(rsi) > 0:
            try:
                i1 = df.index.get_loc(sl.index[-1])
                i2 = df.index.get_loc(sl.index[-2])
                if float(sl.iloc[-1]) < float(sl.iloc[-2]) and float(rsi.iloc[i1]) > float(rsi.iloc[i2]):
                    rsi_div_bull = True
                    trap_score   = max(0, trap_score - 10)  # atenua trap
                    signals.append("Divergência bullish RSI")
            except Exception:
                pass

        trap_probability = min(100, trap_score)

        if fake_breakout and not fake_breakdown:
            trap_dir = "BEARISH_TRAP"
        elif fake_breakdown and not fake_breakout:
            trap_dir = "BULLISH_TRAP"
        elif eq_high_induce and not eq_low_induce:
            trap_dir = "BEARISH_TRAP"
        elif eq_low_induce and not eq_high_induce:
            trap_dir = "BULLISH_TRAP"
        else:
            trap_dir = "NEUTRO"

        return {
            "trap_probability":   trap_probability,
            "trap_direction":     trap_dir,
            "fake_breakout":      fake_breakout,
            "fake_breakdown":     fake_breakdown,
            "eq_high_inducement": eq_high_induce,
            "eq_low_inducement":  eq_low_induce,
            "low_vol_breakout":   low_vol_bo,
            "rsi_div_bear":       rsi_div_bear,
            "rsi_div_bull":       rsi_div_bull,
            "signals":            signals,
        }

    # =========================================================================
    # 6. INSTITUTIONAL SCORE  (7 camadas, 0-100)
    # =========================================================================
    def calculate_institutional_score(
        self,
        trend_score: float       = 50.0,
        correlation_score: float = 50.0,
        volatility_score: float  = 50.0,
    ) -> dict:
        """
        Score 7 camadas: market_structure, liquidity, sweep,
        volume, trend, correlation, volatility.
        trend/correlation/volatility aceitos de fora para integração com
        institutional_scoring.py existente.
        """
        ms  = self._fast_market_structure()
        liq = self.detect_liquidity_pools()
        sw  = self.detect_sweeps()
        vol = self._volume_score()

        layer_scores = {
            "market_structure": ms["score"],
            "liquidity":        liq["score"],
            "sweep":            sw["score"],
            "volume":           vol,
            "trend":            trend_score,
            "correlation":      correlation_score,
            "volatility":       volatility_score,
        }

        total_w = sum(INST_WEIGHTS.values())
        mm_score = round(
            sum(layer_scores[k] * INST_WEIGHTS[k] for k in INST_WEIGHTS) / total_w, 1
        )

        direction   = "LONG" if mm_score > 50 else "SHORT"
        confluences = sum(
            1 for s in layer_scores.values()
            if (s > 60 and direction == "LONG") or (s < 40 and direction == "SHORT")
        )
        valid = confluences >= 4

        strength = (
            "EXTREMO"  if mm_score > 80 or mm_score < 20 else
            "FORTE"    if mm_score > 70 or mm_score < 30 else
            "MODERADO" if mm_score > 60 or mm_score < 40 else
            "NEUTRO"
        )

        return {
            "market_maker_score": mm_score,
            "direction":          direction if valid else "AGUARDANDO",
            "strength":           strength,
            "confluences":        confluences,
            "valid":              valid,
            "layer_scores":       layer_scores,
            "bias":               "BULLISH" if mm_score > 55 else "BEARISH" if mm_score < 45 else "NEUTRO",
        }

    # =========================================================================
    # 7. CONFIDENCE SCORE  (0-100)
    # =========================================================================
    def calculate_confidence(
        self,
        trap_prob: float,
        sweep: dict,
        vol_conf: float,
        trend_alignment: float,
    ) -> float:
        """
        confidence = média ponderada:
          - trend_alignment  40%
          - sweep_strength   25%   (direcional, não absoluto)
          - trap invertido   20%   (alto trap = baixa confiança)
          - volume_conf      15%
        """
        trap_inv  = 100.0 - trap_prob
        sw_conf   = float(sweep["score"])  # já é direcional (>50=bull, <50=bear)

        confidence = (
            trend_alignment * 0.40 +
            sw_conf         * 0.25 +
            trap_inv        * 0.20 +
            vol_conf        * 0.15
        )
        return round(max(0.0, min(100.0, confidence)), 1)

    # =========================================================================
    # FULL ANALYSIS — OUTPUT PRINCIPAL
    # =========================================================================
    def analyze(self) -> dict:
        """
        Executa análise MM completa.
        Output inclui formato requerido + campos extras para o dashboard.
        """
        ms    = self._fast_market_structure()
        liq   = self.detect_liquidity_pools()
        sw    = self.detect_sweeps()
        pd_   = self.detect_premium_discount()
        traps = self.detect_traps()
        vol_s = self._volume_score()

        # MTF (pode falhar sem conexão)
        try:
            mtf            = self.multi_timeframe_bias()
            trend_align    = mtf["weighted_score"]
            inst_bias      = mtf["institutional_bias"]
        except Exception as e:
            log.warning(f"MTF falhou: {e}")
            mtf         = {
                "institutional_bias": "NEUTRAL", "weighted_score": 50.0,
                "bull_weight": 0.0, "bear_weight": 0.0,
                "alignment_pct": 0.0, "details": {}, "structure_map": {},
            }
            trend_align = 50.0
            inst_bias   = "NEUTRAL"

        inst = self.calculate_institutional_score(
            trend_score       = trend_align,
            correlation_score = 50.0,
            volatility_score  = self._volatility_score(),
        )

        confidence = self.calculate_confidence(
            trap_prob       = float(traps["trap_probability"]),
            sweep           = sw,
            vol_conf        = vol_s,
            trend_alignment = trend_align,
        )

        mm_score = inst["market_maker_score"]

        # Bias final
        if mm_score >= 60:   bias = "long"
        elif mm_score <= 40: bias = "short"
        else:                bias = "neutral"

        # Inverter bias se trap alto confirma armadilha
        if traps["trap_probability"] >= 70:
            if traps["trap_direction"] == "BEARISH_TRAP" and bias == "long":
                bias = "neutral"
            elif traps["trap_direction"] == "BULLISH_TRAP" and bias == "short":
                bias = "neutral"

        return {
            # ── Formato principal requerido ───────────────────────────────────
            "bias":                  bias,
            "confidence":            confidence,
            "market_maker_score":    mm_score,
            "trap_probability":      traps["trap_probability"],
            "sweep_strength":        sw["sweep_strength"],
            "liquidity_target_high": liq["liq_target_high"],
            "liquidity_target_low":  liq["liq_target_low"],
            "premium_zone":          pd_["premium_zone"],
            "discount_zone":         pd_["discount_zone"],
            "equilibrium_zone":      pd_["equilibrium_zone"],
            # ── Dados extras ─────────────────────────────────────────────────
            "institutional_bias":    inst_bias,
            "range_position_pct":    pd_["range_position_pct"],
            "equilibrium_price":     pd_["equilibrium"],
            "fib_382":               pd_["fib_382"],
            "fib_618":               pd_["fib_618"],
            "recent_high":           pd_["recent_high"],
            "recent_low":            pd_["recent_low"],
            "trap_direction":        traps["trap_direction"],
            "trap_signals":          traps["signals"],
            "sweep_bias":            sw["sweep_bias"],
            "wick_rejection_bull":   sw["wick_rejection_bull"],
            "wick_rejection_bear":   sw["wick_rejection_bear"],
            "market_structure":      ms["structure"],
            "bos_bull":              ms["bos_bull"],
            "bos_bear":              ms["bos_bear"],
            "choch":                 ms["choch"],
            "equal_highs":           liq["equal_highs"],
            "equal_lows":            liq["equal_lows"],
            "stop_hunt_zones":       liq["stop_hunt_zones"],
            "liq_clusters_high":     liq["clusters_high"],
            "liq_clusters_low":      liq["clusters_low"],
            "mtf_details":           mtf.get("details", {}),
            "layer_scores":          inst["layer_scores"],
            "confluences":           inst["confluences"],
            "valid":                 inst["valid"],
            "strength":              inst["strength"],
        }

    # =========================================================================
    # HELPERS INTERNOS (vetorizados)
    # =========================================================================
    def _compute_swing_highs(self, lookback: int = 5) -> pd.Series:
        """Swing highs vetorizados usando rolling max centrado."""
        h    = self._high
        win  = lookback * 2 + 1
        roll = h.rolling(window=win, center=True, min_periods=win).max()
        mask = (h == roll) & (h > h.shift(1))
        return h[mask].dropna()

    def _compute_swing_lows(self, lookback: int = 5) -> pd.Series:
        """Swing lows vetorizados usando rolling min centrado."""
        l    = self._low
        win  = lookback * 2 + 1
        roll = l.rolling(window=win, center=True, min_periods=win).min()
        mask = (l == roll) & (l < l.shift(1))
        return l[mask].dropna()

    def _atr(self, period: int = 14) -> pd.Series:
        h, l, c = self._high, self._low, self._close
        tr = pd.concat(
            [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
        ).max(axis=1)
        return tr.rolling(period).mean()

    def _rsi(self, period: int = 14) -> pd.Series:
        delta = self._close.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, np.nan)
        return (100 - 100 / (1 + rs)).fillna(50.0)

    def _volume_score(self) -> float:
        vol   = self._volume
        avg_v = float(vol.rolling(20).mean().iloc[-1]) or float(vol.mean())
        rec_v = float(vol.iloc[-3:].mean())
        mult  = rec_v / avg_v if avg_v > 0 else 1.0
        bull  = float(self._close.iloc[-1]) > float(self._close.iloc[-3])
        score = 50
        if mult >= 1.5:   score += 25 if bull else -25
        elif mult >= 1.2: score += 15 if bull else -15
        elif mult < 0.70: score -= 10
        return float(max(0, min(100, score)))

    def _volatility_score(self) -> float:
        atr_pct = self._atr_val / self._price * 100
        if atr_pct < 0.5:   return 70.0
        elif atr_pct < 1.0: return 55.0
        elif atr_pct < 2.0: return 45.0
        else:               return 30.0

    def _fast_market_structure(self) -> dict:
        """
        Market structure rápida (sem recursão MTF) para uso interno.
        """
        sh    = self._sh
        sl    = self._sl
        close = self._close

        structure = "INDEFINIDA"
        bos_bull = bos_bear = choch = False
        hh = hl = lh = ll = False

        if len(sh) >= 2 and len(sl) >= 2:
            hh = float(sh.iloc[-1]) > float(sh.iloc[-2])
            hl = float(sl.iloc[-1]) > float(sl.iloc[-2])
            lh = float(sh.iloc[-1]) < float(sh.iloc[-2])
            ll = float(sl.iloc[-1]) < float(sl.iloc[-2])

            if hh and hl:   structure = "BULLISH"
            elif lh and ll: structure = "BEARISH"
            elif hh and ll: structure = "TRANSIÇÃO"
            else:           structure = "LATERAL"

            bos_bull = (float(close.iloc[-1]) > float(sh.iloc[-1]) and
                        float(close.iloc[-2]) <= float(sh.iloc[-1]))
            bos_bear = (float(close.iloc[-1]) < float(sl.iloc[-1]) and
                        float(close.iloc[-2]) >= float(sl.iloc[-1]))

            if structure == "BULLISH" and bos_bear:
                choch = True; structure = "CHOCH BEARISH"
            elif structure == "BEARISH" and bos_bull:
                choch = True; structure = "CHOCH BULLISH"

        score = 50
        if "BULLISH" in structure:      score += 20
        elif "BEARISH" in structure:    score -= 20
        if bos_bull:                    score += 15
        if bos_bear:                    score -= 15
        if "CHOCH BULLISH" in structure: score += 10
        if "CHOCH BEARISH" in structure: score -= 10
        score = max(0, min(100, score))

        return {
            "structure":       structure,
            "bias":            "BULLISH" if score >= 60 else "BEARISH" if score <= 40 else "NEUTRO",
            "bos_bull":        bos_bull,
            "bos_bear":        bos_bear,
            "choch":           choch,
            "hh": hh, "hl": hl, "lh": lh, "ll": ll,
            "last_swing_high": round(float(sh.iloc[-1]), 2) if len(sh) >= 1 else None,
            "last_swing_low":  round(float(sl.iloc[-1]), 2) if len(sl) >= 1 else None,
            "score":           score,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Função de integração para main.py
# ─────────────────────────────────────────────────────────────────────────────

def run_market_maker_analysis(
    symbol: str,
    df: Optional[pd.DataFrame] = None,
    trend_score: float = 50.0,
    correlation_score: float = 50.0,
) -> dict:
    """
    Entry point para uso em main.py.
    Aceita df externo (evita nova requisição) ou busca internamente.

    Retorna o output padronizado do analyze() + campos de integração.
    """
    from mexc_client import get_ohlcv
    from config import TIMEFRAME

    if df is None:
        sym = symbol if ":" in symbol else symbol + "/USDT:USDT"
        df  = get_ohlcv(sym, TIMEFRAME, limit=300)

    engine = MarketMakerEngine(df, symbol)
    result = engine.analyze()

    # Campos de compatibilidade com institutional_scoring
    result["mm_direction"] = (
        "LONG"  if result["bias"] == "long"  else
        "SHORT" if result["bias"] == "short" else
        "AGUARDANDO"
    )
    result["mm_valid"] = result["valid"]
    result["mm_score"] = result["market_maker_score"]

    return result
