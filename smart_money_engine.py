"""
smart_money_engine.py — Smart Money Concepts Engine (OOP)
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

from config import SYMBOL

log = logging.getLogger(__name__)

# Pesos do score SMC (total = 100)
SMC_WEIGHTS = {
    "market_structure": 20,
    "liquidity":        15,
    "bos":              15,
    "choch":            15,
    "order_blocks":     10,
    "fvg":              10,
    "volume_confirm":   10,
    "momentum":          5,
}

MTF_TIMEFRAMES = {
    "1D":  {"tf": "1d",  "limit": 200},
    "4H":  {"tf": "4h",  "limit": 200},
    "1H":  {"tf": "1h",  "limit": 200},
    "15m": {"tf": "15m", "limit": 300},
}


# ─────────────────────────────────────────────────────────────────────────────

class SmartMoneyEngine:

    def __init__(self, df: pd.DataFrame, symbol: str = "BTC"):
        self.df     = df
        self.symbol = symbol
        self._price = float(df["close"].iloc[-1])

    # =========================
    # Market Structure
    # =========================
    def detect_market_structure(self) -> dict:
        """
        HH/HL/LH/LL + BOS + CHOCH + Sweeps + Equal Highs/Lows.
        """
        df  = self.df
        sh  = self._swing_highs(lookback=5)
        sl  = self._swing_lows(lookback=5)

        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        structure  = "INDEFINIDA"
        bos_bull   = False
        bos_bear   = False
        choch      = False
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

            bos_bull = float(close.iloc[-1]) > float(sh.iloc[-1]) and float(close.iloc[-2]) <= float(sh.iloc[-1])
            bos_bear = float(close.iloc[-1]) < float(sl.iloc[-1]) and float(close.iloc[-2]) >= float(sl.iloc[-1])

            if structure == "BULLISH" and bos_bear:
                choch = True; structure = "CHOCH BEARISH"
            elif structure == "BEARISH" and bos_bull:
                choch = True; structure = "CHOCH BULLISH"

        # Sweeps
        sweep_low = sweep_high = False
        if len(sl) >= 1:
            ref = float(sl.iloc[-1])
            for i in [-1, -2, -3]:
                if float(low.iloc[i]) < ref and float(close.iloc[i]) > ref:
                    sweep_low = True; break
        if len(sh) >= 1:
            ref = float(sh.iloc[-1])
            for i in [-1, -2, -3]:
                if float(high.iloc[i]) > ref and float(close.iloc[i]) < ref:
                    sweep_high = True; break

        # Equal Highs/Lows (liquidez em repouso)
        tol = 0.0015
        equal_highs = len(sh) >= 2 and abs(float(sh.iloc[-1]) - float(sh.iloc[-2])) / float(sh.iloc[-2]) < tol
        equal_lows  = len(sl) >= 2 and abs(float(sl.iloc[-1]) - float(sl.iloc[-2])) / float(sl.iloc[-2]) < tol

        # Fake breakouts
        fake_bull = len(sh) >= 1 and float(high.iloc[-1]) > float(sh.iloc[-1]) and float(close.iloc[-1]) < float(sh.iloc[-1])
        fake_bear = len(sl) >= 1 and float(low.iloc[-1])  < float(sl.iloc[-1]) and float(close.iloc[-1]) > float(sl.iloc[-1])

        # Score
        score = 50
        if "BULLISH" in structure: score += 15
        elif "BEARISH" in structure: score -= 15
        if "CHOCH BULLISH" in structure: score += 10
        elif "CHOCH BEARISH" in structure: score -= 10
        if bos_bull: score += 12
        if bos_bear: score -= 12
        if sweep_low: score += 12
        if sweep_high: score -= 12
        if fake_bear: score += 8
        if fake_bull: score -= 8
        if equal_lows: score += 5
        if equal_highs: score -= 5
        score = max(0, min(100, score))

        return {
            "structure":          structure,
            "hh": bool(hh), "hl": bool(hl), "lh": bool(lh), "ll": bool(ll),
            "bos_bull":           bool(bos_bull),
            "bos_bear":           bool(bos_bear),
            "choch":              bool(choch),
            "sweep_low":          bool(sweep_low),
            "sweep_high":         bool(sweep_high),
            "equal_highs":        bool(equal_highs),
            "equal_lows":         bool(equal_lows),
            "fake_breakout_bull": bool(fake_bull),
            "fake_breakout_bear": bool(fake_bear),
            "last_swing_high":    round(float(sh.iloc[-1]), 2) if len(sh) >= 1 else None,
            "last_swing_low":     round(float(sl.iloc[-1]), 2) if len(sl) >= 1 else None,
            "score":              score,
            "bias":               "BULLISH" if score >= 65 else "BEARISH" if score <= 35 else "NEUTRO",
        }

    # =========================
    # Liquidity Detection
    # =========================
    def detect_liquidity(self) -> dict:
        """
        Nível de liquidez: sweeps, equal highs/lows, fake breakouts.
        Reutiliza o market structure para evitar recalcular swings.
        """
        ms    = self.detect_market_structure()
        score = 50
        if ms["sweep_low"]:          score += 20
        if ms["sweep_high"]:         score -= 20
        if ms["equal_lows"]:         score += 10
        if ms["equal_highs"]:        score -= 10
        if ms["fake_breakout_bear"]: score += 15
        if ms["fake_breakout_bull"]: score -= 15
        score = max(0, min(100, score))

        return {
            "sweep_high":        ms["sweep_high"],
            "sweep_low":         ms["sweep_low"],
            "equal_highs":       ms["equal_highs"],
            "equal_lows":        ms["equal_lows"],
            "fake_breakout_bull": ms["fake_breakout_bull"],
            "fake_breakout_bear": ms["fake_breakout_bear"],
            "score":             score,
            "bias":              "BULLISH" if score > 55 else "BEARISH" if score < 45 else "NEUTRO",
        }

    # =========================
    # Order Block Detection
    # =========================
    def detect_order_blocks(self) -> dict:
        """
        Bullish OB: última vela bearish antes de impulso forte de alta.
        Bearish OB: última vela bullish antes de impulso forte de baixa.
        """
        df        = self.df
        avg_body  = float(abs(df["close"] - df["open"]).tail(20).mean()) or 1.0
        n         = len(df)
        threshold = 1.5
        bullish_ob = bearish_ob = None

        for i in range(max(0, n - 55), max(0, n - 5) - 3):
            c = df.iloc[i]
            bearish_c = float(c["close"]) < float(c["open"])
            bullish_c = float(c["close"]) > float(c["open"])

            if bearish_c:
                bull_imp = sum(
                    max(0.0, float(df.iloc[i+j]["close"]) - float(df.iloc[i+j]["open"]))
                    for j in range(1, 4) if i+j < n
                )
                if bull_imp > threshold * avg_body:
                    ob_h = float(c["high"]); ob_l = float(c["open"])
                    if self._price > ob_l:
                        tested   = bool((df.iloc[i+1:]["low"] < ob_l).any())
                        dist_pct = round((self._price - ob_h) / ob_h * 100, 2)
                        if bullish_ob is None or abs(dist_pct) < abs(bullish_ob["distance_pct"]):
                            bullish_ob = {"high": round(ob_h, 2), "low": round(ob_l, 2),
                                          "mid": round((ob_h+ob_l)/2, 2),
                                          "distance_pct": dist_pct, "tested": tested, "valid": not tested}

            if bullish_c:
                bear_imp = sum(
                    max(0.0, float(df.iloc[i+j]["open"]) - float(df.iloc[i+j]["close"]))
                    for j in range(1, 4) if i+j < n
                )
                if bear_imp > threshold * avg_body:
                    ob_h = float(c["open"]); ob_l = float(c["low"])
                    if self._price < ob_h:
                        tested   = bool((df.iloc[i+1:]["high"] > ob_h).any())
                        dist_pct = round((ob_l - self._price) / ob_l * 100, 2)
                        if bearish_ob is None or abs(dist_pct) < abs(bearish_ob["distance_pct"]):
                            bearish_ob = {"high": round(ob_h, 2), "low": round(ob_l, 2),
                                          "mid": round((ob_h+ob_l)/2, 2),
                                          "distance_pct": dist_pct, "tested": tested, "valid": not tested}

        # Score
        score = 50
        if bullish_ob and bullish_ob["valid"]:
            d = bullish_ob["distance_pct"]
            score += 30 if d <= 0 else 18 if d <= 3 else 7
        if bearish_ob and bearish_ob["valid"]:
            d = bearish_ob["distance_pct"]
            score -= 30 if d <= 0 else 18 if d <= 3 else 7
        score = max(0, min(100, score))

        return {
            "bullish_ob": bullish_ob,
            "bearish_ob": bearish_ob,
            "score":      score,
            "bias":       "BULLISH" if score > 55 else "BEARISH" if score < 45 else "NEUTRO",
        }

    # =========================
    # Fair Value Gap
    # =========================
    def detect_fvg(self) -> dict:
        """
        Bullish FVG: c[i-1].high < c[i+1].low
        Bearish FVG: c[i-1].low  > c[i+1].high
        """
        df     = self.df
        n      = len(df)
        bull_fvgs: list = []
        bear_fvgs: list = []

        for idx in range(max(1, n - 101), n - 1):
            c_prev = df.iloc[idx - 1]
            c_next = df.iloc[idx + 1]

            # Bullish
            gl, gh = float(c_prev["high"]), float(c_next["low"])
            if gh > gl:
                pct = (gh - gl) / gl * 100
                if pct >= 0.05:
                    filled = bool((df.iloc[idx+2:]["low"] <= gl).any())
                    mid    = (gh + gl) / 2
                    bull_fvgs.append({"high": round(gh,2), "low": round(gl,2), "mid": round(mid,2),
                                      "gap_pct": round(pct,3), "filled": filled,
                                      "distance_pct": round((self._price - mid)/mid*100, 2)})

            # Bearish
            gh2, gl2 = float(c_prev["low"]), float(c_next["high"])
            if gh2 > gl2:
                pct = (gh2 - gl2) / gl2 * 100
                if pct >= 0.05:
                    filled = bool((df.iloc[idx+2:]["high"] >= gh2).any())
                    mid    = (gh2 + gl2) / 2
                    bear_fvgs.append({"high": round(gh2,2), "low": round(gl2,2), "mid": round(mid,2),
                                      "gap_pct": round(pct,3), "filled": filled,
                                      "distance_pct": round((mid - self._price)/mid*100, 2)})

        active_bull = sorted([f for f in bull_fvgs if not f["filled"]], key=lambda x: abs(x["distance_pct"]))
        active_bear = sorted([f for f in bear_fvgs if not f["filled"]], key=lambda x: abs(x["distance_pct"]))

        fvg_below = next((f for f in active_bull if f["distance_pct"] >= 0), None)
        fvg_above = next((f for f in active_bear if f["distance_pct"] >= 0), None)

        # Score
        score = 50
        if fvg_below:
            d = fvg_below["distance_pct"]
            score += 28 if d <= 1 else 17 if d <= 3 else 8 if d <= 6 else 0
        if fvg_above:
            d = fvg_above["distance_pct"]
            score -= 28 if d <= 1 else 17 if d <= 3 else 8 if d <= 6 else 0
        score = max(0, min(100, score))

        return {
            "bullish_fvg":      len(active_bull) > 0,
            "bearish_fvg":      len(active_bear) > 0,
            "bullish_fvgs":     active_bull[:3],
            "bearish_fvgs":     active_bear[:3],
            "nearest_bull_fvg": fvg_below,
            "nearest_bear_fvg": fvg_above,
            "total_bull":       len(active_bull),
            "total_bear":       len(active_bear),
            "score":            score,
            "bias":             "BULLISH" if score > 55 else "BEARISH" if score < 45 else "NEUTRO",
        }

    # =========================
    # Multi Timeframe
    # =========================
    def multi_timeframe_analysis(self) -> dict:
        """
        Roda detect_market_structure() nos 4 TFs (1D/4H/1H/15m).
        """
        from mexc_client import get_ohlcv

        results     = {}
        tf_weights  = {"1D": 0.30, "4H": 0.30, "1H": 0.25, "15m": 0.15}

        for label, cfg in MTF_TIMEFRAMES.items():
            try:
                df_tf = get_ohlcv(self.symbol if ":" in self.symbol else self.symbol + "/USDT:USDT",
                                  cfg["tf"], limit=cfg["limit"])
                eng   = SmartMoneyEngine(df_tf, self.symbol)
                ms    = eng.detect_market_structure()
                ob    = eng.detect_order_blocks()
                fvg   = eng.detect_fvg()
                results[label] = {
                    "structure": ms["structure"],
                    "bias":      ms["bias"],
                    "bos_bull":  ms["bos_bull"],
                    "bos_bear":  ms["bos_bear"],
                    "choch":     ms["choch"],
                    "ob":        ob,
                    "fvg":       {"nearest_bull": fvg["nearest_bull_fvg"], "nearest_bear": fvg["nearest_bear_fvg"]},
                    "score":     ms["score"],
                }
                log.info(f"   MTF {label}: {ms['structure']} | score={ms['score']}")
            except Exception as e:
                log.warning(f"   MTF {label} falhou: {e}")
                results[label] = {"structure": "?", "bias": "NEUTRO", "score": 50}

        biases     = [results.get(tf, {}).get("bias", "NEUTRO") for tf in MTF_TIMEFRAMES]
        bull_count = biases.count("BULLISH")
        bear_count = biases.count("BEARISH")

        if   bull_count >= 3: alignment, strength = "BULLISH", bull_count
        elif bear_count >= 3: alignment, strength = "BEARISH", bear_count
        elif bull_count == 2 and bear_count == 0: alignment, strength = "BULLISH_WEAK", 2
        elif bear_count == 2 and bull_count == 0: alignment, strength = "BEARISH_WEAK", 2
        else:                                     alignment, strength = "MIXED", 0

        composite = round(sum(
            results.get(tf, {}).get("score", 50) * tf_weights.get(tf, 0.25)
            for tf in tf_weights
        ), 1)

        confidence_map = {4: "EXTREMAMENTE ALTA", 3: "ALTA", 2: "MODERADA", 0: "BAIXA", 1: "BAIXA"}
        confidence = confidence_map.get(strength, "BAIXA")

        return {
            "mtf_bias":   alignment.replace("_WEAK", ""),
            "alignment":  alignment,
            "strength":   strength,
            "confidence": confidence,
            "composite":  composite,
            "details":    results,
            "structure": {tf: results.get(tf, {}).get("structure", "?") for tf in MTF_TIMEFRAMES},
        }

    # =========================
    # Score Calculation
    # =========================
    def calculate_score(self) -> dict:
        """
        Score SMC ponderado 0–100 em 8 camadas.
        """
        ms  = self.detect_market_structure()
        liq = self.detect_liquidity()
        ob  = self.detect_order_blocks()
        fvg = self.detect_fvg()

        # BOS score
        bos_score = 50
        if ms["bos_bull"]: bos_score += 35
        if ms["bos_bear"]: bos_score -= 35
        bos_score = max(0, min(100, bos_score))

        # CHOCH score
        choch_score = 50
        if "CHOCH BULLISH" in ms["structure"]: choch_score = 80
        elif "CHOCH BEARISH" in ms["structure"]: choch_score = 20

        # Volume
        vol   = self.df["volume"]
        close = self.df["close"]
        avg_v = float(vol.rolling(20).mean().iloc[-1]) or float(vol.mean())
        rec_v = float(vol.iloc[-3:].mean())
        vol_score = 50
        mult  = rec_v / avg_v if avg_v > 0 else 1.0
        if mult >= 1.5: vol_score += 20 if float(close.iloc[-1]) > float(close.iloc[-3]) else -20
        elif mult >= 1.2: vol_score += 10 if float(close.iloc[-1]) > float(close.iloc[-3]) else -10
        vol_score = max(0, min(100, vol_score))

        # Momentum (RSI 14)
        delta = self.df["close"].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi14 = float((100 - 100/(1+rs)).iloc[-1]) if not pd.isna((100 - 100/(1+rs)).iloc[-1]) else 50.0
        if rsi14 >= 60: mom_score = min(100, int(50 + (rsi14 - 50) * 1.5))
        elif rsi14 <= 40: mom_score = max(0,  int(50 - (50 - rsi14) * 1.5))
        else: mom_score = 50

        layer_scores = {
            "market_structure": ms["score"],
            "liquidity":        liq["score"],
            "bos":              bos_score,
            "choch":            choch_score,
            "order_blocks":     ob["score"],
            "fvg":              fvg["score"],
            "volume_confirm":   vol_score,
            "momentum":         mom_score,
        }

        weighted_sum = sum(layer_scores[k] * SMC_WEIGHTS[k] for k in SMC_WEIGHTS)
        smc_score    = round(weighted_sum / sum(SMC_WEIGHTS.values()), 1)

        direction   = "LONG" if smc_score > 50 else "SHORT"
        confluences = sum(1 for s in layer_scores.values()
                         if (s > 55 and direction == "LONG") or (s < 45 and direction == "SHORT"))
        valid = confluences >= 4

        return {
            "smc_score":    smc_score,
            "layer_scores": layer_scores,
            "confluences":  confluences,
            "valid":        valid,
            "direction":    direction if valid else "AGUARDANDO",
            "bias":         "BULLISH" if smc_score >= 60 else "BEARISH" if smc_score <= 40 else "NEUTRO",
        }

    # =========================
    # Generate Signal
    # =========================
    def generate_signal(self) -> dict:
        """
        Entry, stop e alvos baseados em ATR + refinamento por OB/FVG.
        """
        score_data = self.calculate_score()
        ob         = self.detect_order_blocks()
        fvg        = self.detect_fvg()

        direction = score_data["direction"]
        entry     = self._price

        # ATR 14
        atr_ser = self._atr(14)
        atr     = float(atr_ser.iloc[-1]) if not pd.isna(atr_ser.iloc[-1]) else self._price * 0.005

        if direction == "LONG":
            stop = entry - atr * 1.5
            tp1  = entry + atr * 1.5
            tp2  = entry + atr * 2.5
            tp3  = entry + atr * 4.0
            # Refinamento: OB bullish próximo
            if (ob["bullish_ob"] or {}).get("valid") and -2.0 < (ob["bullish_ob"] or {}).get("distance_pct", 99) <= 0.5:
                b = ob["bullish_ob"]
                entry = round((b["high"] + b["low"]) / 2, 2)
                stop  = round(b["low"] - atr * 0.5, 2)
            # Refinamento: FVG bullish próximo
            elif fvg["nearest_bull_fvg"] and 0 <= fvg["nearest_bull_fvg"].get("distance_pct", 99) <= 1.0:
                entry = round(fvg["nearest_bull_fvg"]["mid"], 2)
        elif direction == "SHORT":
            stop = entry + atr * 1.5
            tp1  = entry - atr * 1.5
            tp2  = entry - atr * 2.5
            tp3  = entry - atr * 4.0
            if (ob["bearish_ob"] or {}).get("valid") and -2.0 < (ob["bearish_ob"] or {}).get("distance_pct", 99) <= 0.5:
                b = ob["bearish_ob"]
                entry = round((b["high"] + b["low"]) / 2, 2)
                stop  = round(b["high"] + atr * 0.5, 2)
            elif fvg["nearest_bear_fvg"] and 0 <= fvg["nearest_bear_fvg"].get("distance_pct", 99) <= 1.0:
                entry = round(fvg["nearest_bear_fvg"]["mid"], 2)
        else:
            stop = tp1 = tp2 = tp3 = None

        risk = abs(entry - stop) if stop else atr
        rr   = lambda tp: round(abs(tp - entry) / risk, 2) if tp and risk > 0 else None

        return {
            "direction": direction.lower() if direction in ("LONG","SHORT") else "neutral",
            "score":     score_data["smc_score"],
            "valid":     score_data["valid"],
            "entry":     float(round(entry, 2)),
            "stop":      float(round(stop, 2)) if stop else None,
            "tp1":       float(round(tp1, 2))  if tp1  else None,
            "tp2":       float(round(tp2, 2))  if tp2  else None,
            "tp3":       float(round(tp3, 2))  if tp3  else None,
            "rr1":       rr(tp1), "rr2": rr(tp2), "rr3": rr(tp3),
            "atr":       round(atr, 2),
        }

    # =========================
    # Full Analysis
    # =========================
    def analyze(self) -> dict:
        """
        Executa análise SMC completa e retorna dict estruturado.
        """
        ms     = self.detect_market_structure()
        liq    = self.detect_liquidity()
        ob     = self.detect_order_blocks()
        fvg    = self.detect_fvg()
        score  = self.calculate_score()
        signal = self.generate_signal()

        # MTF — pode falhar em ambientes sem conexão
        try:
            mtf = self.multi_timeframe_analysis()
        except Exception as e:
            log.warning(f"MTF falhou: {e}")
            mtf = {"mtf_bias": "?", "alignment": "MIXED", "confidence": "BAIXA",
                   "composite": score["smc_score"], "structure": {}}

        # Reasoning
        parts = []
        for tf, s in mtf.get("structure", {}).items():
            if s and s != "?": parts.append(f"{tf}: {s}")
        if ms["bos_bull"] and signal["direction"] == "long":  parts.append("BOS bull ✓")
        if ms["bos_bear"] and signal["direction"] == "short": parts.append("BOS bear ✓")
        if ms["choch"]: parts.append("CHOCH ✓")
        if (ob["bullish_ob"] or {}).get("valid") and signal["direction"] == "long":  parts.append("OB bull ✓")
        if (ob["bearish_ob"] or {}).get("valid") and signal["direction"] == "short": parts.append("OB bear ✓")
        if fvg["nearest_bull_fvg"] and signal["direction"] == "long":
            g = fvg["nearest_bull_fvg"]
            parts.append(f"FVG bull {g['gap_pct']:.2f}%")
        parts.append(f"Score: {score['smc_score']:.1f}/100 ({mtf.get('alignment','?')})")

        return {
            "market_structure": ms,
            "liquidity":        liq,
            "order_blocks":     ob,
            "fvg":              fvg,
            "score":            score,
            "signal":           signal,
            "mtf":              mtf,
            # Campos prontos para o dashboard
            "smc_score":  score["smc_score"],
            "bias":       score["bias"],
            "direction":  signal["direction"].upper() if signal["direction"] != "neutral" else "AGUARDANDO",
            "alignment":  mtf.get("alignment", "MIXED"),
            "confidence": mtf.get("confidence", "BAIXA"),
            "valid":      signal["valid"],
            "entry":      signal["entry"],
            "stop":       signal["stop"],
            "targets":    {"tp1": signal["tp1"], "rr1": signal["rr1"],
                           "tp2": signal["tp2"], "rr2": signal["rr2"],
                           "tp3": signal["tp3"], "rr3": signal["rr3"]},
            "structure":  mtf.get("structure", {}),
            "reasoning":  " | ".join(parts),
        }

    # =========================
    # Helpers internos
    # =========================
    def _swing_highs(self, lookback: int = 5) -> pd.Series:
        highs = self.df["high"]
        mask  = pd.Series(False, index=highs.index)
        for i in range(lookback, len(highs) - lookback):
            w = highs.iloc[i - lookback: i + lookback + 1]
            if highs.iloc[i] == w.max() and highs.iloc[i] > highs.iloc[i-1]:
                mask.iloc[i] = True
        return highs[mask].dropna()

    def _swing_lows(self, lookback: int = 5) -> pd.Series:
        lows = self.df["low"]
        mask = pd.Series(False, index=lows.index)
        for i in range(lookback, len(lows) - lookback):
            w = lows.iloc[i - lookback: i + lookback + 1]
            if lows.iloc[i] == w.min() and lows.iloc[i] < lows.iloc[i-1]:
                mask.iloc[i] = True
        return lows[mask].dropna()

    def _atr(self, period: int = 14) -> pd.Series:
        h, l, c = self.df["high"], self.df["low"], self.df["close"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        return tr.rolling(period).mean()
