"""
scripts/test_regime_direction.py

Teste direcional do RegimeDetector (FIX A do neural_engine).

Gera DataFrame sintetico bearish e bullish, chama RegimeDetector.detect()
diretamente (bypassa extract_features), valida:
  - bearish: regime == TRENDING_DOWN, bear_prob > bull_prob
  - bullish: regime == TRENDING_UP, bull_prob > bear_prob

Saida nao-zero se alguma asserção falhar.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from neural_engine.regime_detector import (
    RegimeDetector,
    REGIME_TRENDING_UP,
    REGIME_TRENDING_DOWN,
)
from neural_engine.feature_engine import FeatureSet


def _make_df(direction: str, n: int = 50) -> pd.DataFrame:
    """Cria OHLCV sintetico com tendencia direcional."""
    if direction == "bearish":
        closes = np.linspace(100.0, 90.0, n)      # cai 10%
    elif direction == "bullish":
        closes = np.linspace(90.0, 100.0, n)      # sobe 11%
    else:
        closes = np.full(n, 95.0) + np.random.normal(0, 0.1, n)

    df = pd.DataFrame({
        "open":   closes * 1.001,
        "high":   closes * 1.003,
        "low":    closes * 0.997,
        "close":  closes,
        "volume": np.random.uniform(100, 200, n),
    })
    return df


def _make_empty_feature_set() -> FeatureSet:
    """FeatureSet vazio suficiente pra chamar detect() — não é usado por _score_trending."""
    return FeatureSet(
        sequence=np.zeros((64, 32)),
        tabular=np.zeros(48),
        mtf_matrix=np.zeros((4, 32)),
        valid=True,
    )


def _test_case(name: str, df: pd.DataFrame, regime_data: dict, trend_data: dict,
               expected_regime: str, expected_higher: str) -> tuple[bool, str]:
    rd = RegimeDetector()
    fs = _make_empty_feature_set()
    result = rd.detect(
        df=df,
        feature_set=fs,
        regime_data=regime_data,
        trend_data=trend_data,
    )
    out = result.to_neural_output()

    regime_ok  = result.regime == expected_regime
    if expected_higher == "bear":
        prob_ok = out["bear_probability"] > out["bull_probability"]
    else:
        prob_ok = out["bull_probability"] > out["bear_probability"]

    msg = (
        f"[{name}] regime={result.regime} (esperado {expected_regime}) | "
        f"bull={out['bull_probability']} bear={out['bear_probability']} "
        f"side={out['sideways_probability']} | "
        f"trending_up_score={result.sub_regimes.get(REGIME_TRENDING_UP):.1f} "
        f"trending_down_score={result.sub_regimes.get(REGIME_TRENDING_DOWN):.1f}"
    )
    return regime_ok and prob_ok, msg


def main() -> int:
    print("=" * 70)
    print("  TEST — Regime Detector direcional (FIX A)")
    print("=" * 70)

    failures = 0

    # Caso 1: Tendencia de BAIXA forte
    df_bear = _make_df("bearish", n=50)
    ok, msg = _test_case(
        name="bearish_strong",
        df=df_bear,
        regime_data={"adx": 35, "atr_pct": 2.5},
        trend_data={"ema_signal": "BEAR_ALIGNED", "ichimoku_below_cloud": True},
        expected_regime=REGIME_TRENDING_DOWN,
        expected_higher="bear",
    )
    print(("OK  " if ok else "FAIL") + " " + msg)
    failures += 0 if ok else 1

    # Caso 2: Tendencia de ALTA forte
    df_bull = _make_df("bullish", n=50)
    ok, msg = _test_case(
        name="bullish_strong",
        df=df_bull,
        regime_data={"adx": 35, "atr_pct": 2.5},
        trend_data={"ema_signal": "BULL_ALIGNED", "ichimoku_above_cloud": True},
        expected_regime=REGIME_TRENDING_UP,
        expected_higher="bull",
    )
    print(("OK  " if ok else "FAIL") + " " + msg)
    failures += 0 if ok else 1

    # Caso 3: Tendencia de BAIXA com EMA ainda bullish (sinais mistos — slope dominates)
    df_bear_mixed = _make_df("bearish", n=50)
    ok, msg = _test_case(
        name="bearish_mixed_ema",
        df=df_bear_mixed,
        regime_data={"adx": 30, "atr_pct": 2.0},
        trend_data={"ema_signal": "BULL_WEAK", "ichimoku_above_cloud": False},
        expected_regime=REGIME_TRENDING_DOWN,
        expected_higher="bear",
    )
    print(("OK  " if ok else "FAIL") + " " + msg)
    failures += 0 if ok else 1

    print("=" * 70)
    if failures == 0:
        print("  PASSOU: FIX A valida — regime é direcional.")
        return 0
    else:
        print(f"  FALHOU: {failures} caso(s) com regime/probabilidade errados.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
