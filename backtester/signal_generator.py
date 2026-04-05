"""
backtester/signal_generator.py — BacktestSME

Subclasse de SmartMoneyEngine que sobreescreve multi_timeframe_analysis()
para usar DataFrames pré-carregados em vez de chamadas live ao CCXT.
Isso elimina look-ahead bias: para cada passo temporal T, só usa dados
disponíveis até T.
"""
import logging
import sys
import os
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from smart_money_engine import SmartMoneyEngine, MTF_TIMEFRAMES

log = logging.getLogger(__name__)


class BacktestSME(SmartMoneyEngine):
    """
    SmartMoneyEngine adaptado para backtesting.

    Parâmetros adicionais no construtor:
        preloaded_dfs  — dict {tf_label: pd.DataFrame} com dados de todos os TFs
        current_ts     — pd.Timestamp: ponto temporal atual (sem look-ahead)
    """

    def __init__(self, df: pd.DataFrame, symbol: str,
                 preloaded_dfs: dict, current_ts: pd.Timestamp):
        super().__init__(df, symbol)
        self._preloaded_dfs = preloaded_dfs
        self._current_ts    = current_ts

    # Mapeamento label → chave em preloaded_dfs
    _TF_KEY = {"1D": "1d", "4H": "4h", "1H": "1h", "15m": "15m"}

    def multi_timeframe_analysis(self) -> dict:
        """
        Sobreescreve o método original para usar dados pre-fetched.
        Filtra cada DataFrame até current_ts para evitar look-ahead.
        """
        tf_weights = {"1D": 0.30, "4H": 0.30, "1H": 0.25, "15m": 0.15}
        results    = {}

        for label in MTF_TIMEFRAMES:
            tf_key = self._TF_KEY.get(label)
            df_full = self._preloaded_dfs.get(tf_key)

            if df_full is None or df_full.empty:
                results[label] = {"structure": "?", "bias": "NEUTRO", "score": 50}
                continue

            try:
                # Fatia até current_ts — zero look-ahead bias
                slice_df = df_full[df_full.index <= self._current_ts].iloc[-200:]

                if len(slice_df) < 20:
                    results[label] = {"structure": "?", "bias": "NEUTRO", "score": 50}
                    continue

                eng = SmartMoneyEngine(slice_df, self.symbol)
                ms  = eng.detect_market_structure()
                ob  = eng.detect_order_blocks()
                fvg = eng.detect_fvg()

                results[label] = {
                    "structure": ms["structure"],
                    "bias":      ms["bias"],
                    "bos_bull":  ms["bos_bull"],
                    "bos_bear":  ms["bos_bear"],
                    "choch":     ms["choch"],
                    "ob":        ob,
                    "fvg":       {
                        "nearest_bull": fvg["nearest_bull_fvg"],
                        "nearest_bear": fvg["nearest_bear_fvg"],
                    },
                    "score": ms["score"],
                }
            except Exception as e:
                log.debug(f"[BacktestSME] MTF {label} falhou: {e}")
                results[label] = {"structure": "?", "bias": "NEUTRO", "score": 50}

        biases     = [results.get(tf, {}).get("bias", "NEUTRO") for tf in MTF_TIMEFRAMES]
        bull_count = biases.count("BULLISH")
        bear_count = biases.count("BEARISH")

        if   bull_count >= 3: alignment, strength = "BULLISH",      bull_count
        elif bear_count >= 3: alignment, strength = "BEARISH",      bear_count
        elif bull_count == 2 and bear_count == 0: alignment, strength = "BULLISH_WEAK", 2
        elif bear_count == 2 and bull_count == 0: alignment, strength = "BEARISH_WEAK", 2
        else:                                     alignment, strength = "MIXED",        0

        composite = round(sum(
            results.get(tf, {}).get("score", 50) * tf_weights.get(tf, 0.25)
            for tf in tf_weights
        ), 1)

        confidence_map = {4: "EXTREMAMENTE ALTA", 3: "ALTA", 2: "MODERADA", 0: "BAIXA", 1: "BAIXA"}

        return {
            "mtf_bias":   alignment.replace("_WEAK", ""),
            "alignment":  alignment,
            "strength":   strength,
            "confidence": confidence_map.get(strength, "BAIXA"),
            "composite":  composite,
            "details":    results,
            "structure":  {tf: results.get(tf, {}).get("structure", "?") for tf in MTF_TIMEFRAMES},
        }


def generate_signal_at(df_window: pd.DataFrame, symbol: str,
                       preloaded_dfs: dict, current_ts: pd.Timestamp) -> Optional[dict]:
    """
    Gera sinal SMC no instante current_ts usando a janela df_window.

    Retorna dict com os campos do sinal, ou None se sinal inválido.
    """
    try:
        engine = BacktestSME(df_window, symbol, preloaded_dfs, current_ts)
        result = engine.analyze()

        sig = result.get("signal", {})
        if not sig.get("valid"):
            return None

        direction = sig.get("direction", "neutral")
        if direction == "neutral":
            return None

        return {
            "timestamp":   current_ts,
            "direction":   direction.upper(),
            "smc_score":   result.get("smc_score", 0),
            "confluences": result.get("score", {}).get("confluences", 0),
            "alignment":   result.get("alignment", "MIXED"),
            "entry":       sig.get("entry"),
            "stop":        sig.get("stop"),
            "tp1":         sig.get("tp1"),
            "tp2":         sig.get("tp2"),
            "tp3":         sig.get("tp3"),
            "rr1":         sig.get("rr1"),
            "rr2":         sig.get("rr2"),
            "rr3":         sig.get("rr3"),
            "atr":         sig.get("atr"),
        }
    except Exception as e:
        log.debug(f"[generate_signal_at] erro em {current_ts}: {e}")
        return None
