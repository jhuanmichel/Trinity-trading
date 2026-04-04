"""
feature_engine.py — Motor de Extração e Normalização de Features
Cap. 17 | Trinity Neural Intelligence Engine v2

Responsabilidades:
  - Coleta outputs de todos os motores Trinity (institucional, SMC, ciclo, geo, direção)
  - Extrai features derivadas de OHLCV (retornos, volatilidade realizada, etc.)
  - Normaliza via Z-score + clipping para garantir estabilidade numérica
  - Monta vetores separados para: sequência temporal, tabular, multi-timeframe

Outputs:
  FeatureSet.sequence   → np.ndarray [N, D] para LSTM/CNN
  FeatureSet.tabular    → np.ndarray [F]    para MLP
  FeatureSet.mtf_matrix → np.ndarray [T, D] para Transformer
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────
SEQUENCE_LEN  = 64   # janela temporal (barras) para LSTM / CNN
MTF_TIMEFRAMES = 4   # 1D, 4H, 1H, 15m
FEATURE_DIM    = 32  # dimensão por barra na sequência
TABULAR_DIM    = 48  # dimensão do vetor tabular para MLP

# Clip Z-score: previne explosão de gradientes em outliers extremos
Z_CLIP = 4.0


# ─── Estrutura de saída ────────────────────────────────────────────────────────
@dataclass
class FeatureSet:
    """Container para todos os vetores de feature prontos para inferência."""
    sequence:    np.ndarray              # [SEQUENCE_LEN, FEATURE_DIM]
    tabular:     np.ndarray              # [TABULAR_DIM]
    mtf_matrix:  np.ndarray              # [MTF_TIMEFRAMES, FEATURE_DIM]
    raw_scalars: Dict[str, float] = field(default_factory=dict)
    valid:       bool = True
    error:       Optional[str] = None


# ─── Helpers ───────────────────────────────────────────────────────────────────
def _safe(val, default=0.0) -> float:
    """Converte valor para float seguro (NaN / None → default)."""
    try:
        v = float(val)
        return default if (np.isnan(v) or np.isinf(v)) else v
    except Exception:
        return default


def _zscore_clip(arr: np.ndarray, clip: float = Z_CLIP) -> np.ndarray:
    """Z-score normalização com clipping para robustez contra outliers."""
    arr = np.array(arr, dtype=np.float32)
    mu  = np.nanmean(arr, axis=0)
    sigma = np.nanstd(arr, axis=0) + 1e-8
    norm = (arr - mu) / sigma
    return np.clip(norm, -clip, clip)


def _minmax_01(arr: np.ndarray) -> np.ndarray:
    """Min-max normalização para [0, 1]."""
    arr = np.array(arr, dtype=np.float32)
    mn, mx = arr.min(), arr.max()
    if mx - mn < 1e-8:
        return np.zeros_like(arr)
    return (arr - mn) / (mx - mn)


# ─── Extração de features OHLCV ───────────────────────────────────────────────
def _extract_ohlcv_sequence(df: pd.DataFrame) -> np.ndarray:
    """
    Extrai matriz [N, FEATURE_DIM] de features por barra OHLCV.
    Inclui: retornos, body ratio, wick ratio, velas derivadas,
    momentum local, volatilidade rolling, volume delta proxy.
    """
    n  = min(SEQUENCE_LEN, len(df))
    df = df.tail(n).copy().reset_index(drop=True)

    close  = df["close"].values.astype(np.float32)
    open_  = df["open"].values.astype(np.float32)
    high   = df["high"].values.astype(np.float32)
    low    = df["low"].values.astype(np.float32)
    volume = df["volume"].values.astype(np.float32) + 1e-8

    # Retornos log
    log_ret = np.diff(np.log(close + 1e-8), prepend=np.log(close[0]))

    # Body / range ratios
    body       = np.abs(close - open_)
    rng        = high - low + 1e-8
    body_ratio = body / rng
    wick_up    = (high - np.maximum(close, open_)) / rng
    wick_dn    = (np.minimum(close, open_) - low)   / rng

    # CLV (Close Location Value — proxy de pressão direcional)
    clv = ((close - low) - (high - close)) / rng

    # Retorno vs ATR rolling (momentum local normalizado)
    atr_roll = pd.Series(rng).rolling(14, min_periods=1).mean().values + 1e-8
    mom_norm = log_ret / atr_roll

    # Volatilidade realizada rolling (20 barras)
    vol_roll = pd.Series(log_ret).rolling(20, min_periods=1).std().fillna(0).values

    # Volume normalizado
    vol_norm = _minmax_01(volume)

    # Volume-Weighted Price deviation
    vwap_proxy = (high + low + close) / 3
    vwap_dev   = (close - vwap_proxy) / (rng + 1e-8)

    # Bollinger Band position proxy
    sma20 = pd.Series(close).rolling(20, min_periods=1).mean().values
    std20 = pd.Series(close).rolling(20, min_periods=1).std().fillna(0).values + 1e-8
    bb_pos = (close - sma20) / (2 * std20)
    bb_pos = np.clip(bb_pos, -3, 3) / 3.0

    # RSI proxy (sem biblioteca)
    gains = np.maximum(log_ret, 0)
    losses = np.maximum(-log_ret, 0)
    avg_gain = pd.Series(gains).rolling(14, min_periods=1).mean().values + 1e-8
    avg_loss = pd.Series(losses).rolling(14, min_periods=1).mean().values + 1e-8
    rs = avg_gain / avg_loss
    rsi_norm = (100 - 100 / (1 + rs)) / 100.0

    # Tendência de curto prazo: EMA8 vs EMA21
    ema8  = pd.Series(close).ewm(span=8,  adjust=False).mean().values
    ema21 = pd.Series(close).ewm(span=21, adjust=False).mean().values
    ema_cross = np.sign(ema8 - ema21) * np.abs(ema8 - ema21) / (close + 1e-8)

    # Montagem da matriz [N, FEATURE_DIM]
    # 16 features por barra → pad até FEATURE_DIM com zeros
    features = np.stack([
        _zscore_clip(log_ret),     # 0  retorno log
        body_ratio,                # 1  body/range
        wick_up,                   # 2  sombra superior
        wick_dn,                   # 3  sombra inferior
        clv,                       # 4  close location value
        _zscore_clip(mom_norm),    # 5  momentum normalizado
        _zscore_clip(vol_roll),    # 6  volatilidade realizada
        vol_norm,                  # 7  volume normalizado
        vwap_dev,                  # 8  desvio VWAP
        bb_pos,                    # 9  posição Bollinger Band
        rsi_norm,                  # 10 RSI normalizado
        _zscore_clip(ema_cross),   # 11 cruzamento EMA
    ], axis=1).astype(np.float32)

    # Pad se FEATURE_DIM > features.shape[1]
    pad_cols = FEATURE_DIM - features.shape[1]
    if pad_cols > 0:
        features = np.concatenate(
            [features, np.zeros((features.shape[0], pad_cols), dtype=np.float32)],
            axis=1
        )

    # Pad se sequência menor que SEQUENCE_LEN
    if features.shape[0] < SEQUENCE_LEN:
        pad_rows = SEQUENCE_LEN - features.shape[0]
        features = np.concatenate(
            [np.zeros((pad_rows, FEATURE_DIM), dtype=np.float32), features],
            axis=0
        )

    return features.astype(np.float32)


# ─── Extração de features tabulares (motores Trinity) ─────────────────────────
def _extract_tabular_features(
    price:           float,
    inst_data:       Dict[str, Any],
    smc_data:        Optional[Dict],
    mm_data:         Optional[Dict],
    pressure_data:   Optional[Dict],
    rare_data:       Optional[Dict],
    geo_data:        Optional[Dict],
    cycle_data:      Optional[Dict],
    direction_data:  Optional[Dict],
    regime_data:     Optional[Dict],
    volume_data:     Optional[Dict],
    trend_data:      Optional[Dict],
    deriv_data:      Optional[Dict],
) -> np.ndarray:
    """
    Extrai vetor tabular [TABULAR_DIM] com todos os scores dos motores Trinity.
    Features escalares normalizadas para [0, 1] ou [-1, 1].
    """
    # Helper para extrair score de 0-100 → 0-1
    def s(d, key, default=50.0): return _safe(d.get(key, default) if d else default) / 100.0
    def b(d, key): return 1.0 if (d and d.get(key)) else 0.0
    def sign_score(d, key, default=0.0):
        return np.clip(_safe(d.get(key, default) if d else default) / 100.0, -1, 1)

    feats = []

    # ── Institutional Score (6 features) ─────────────────────────────────────
    feats.append(s(inst_data, "inst_score"))
    feats.append(_safe(inst_data.get("confluences", 0) if inst_data else 0) / 6.0)
    direction_map = {"LONG": 1.0, "SHORT": -1.0, "AGUARDANDO": 0.0, "NEUTRAL": 0.0}
    feats.append(direction_map.get(inst_data.get("direction", "NEUTRAL") if inst_data else "NEUTRAL", 0.0))
    feats.append(1.0 if (inst_data and inst_data.get("valid")) else 0.0)
    layer_sc = inst_data.get("layer_scores", {}) if inst_data else {}
    feats.append(s(layer_sc, "market_structure"))
    feats.append(s(layer_sc, "volume"))

    # ── SMC (4 features) ─────────────────────────────────────────────────────
    feats.append(s(smc_data, "smc_score"))
    feats.append(b(smc_data, "valid"))
    smc_dir_map = {"LONG": 1.0, "SHORT": -1.0, "NEUTRAL": 0.0}
    feats.append(smc_dir_map.get(smc_data.get("direction", "NEUTRAL") if smc_data else "NEUTRAL", 0.0))
    feats.append(s(smc_data, "confidence"))

    # ── Market Maker (5 features) ─────────────────────────────────────────────
    feats.append(s(mm_data, "market_maker_score"))
    feats.append(b(mm_data, "valid"))
    trap_prob = _safe(mm_data.get("trap_probability", 0) if mm_data else 0) / 100.0
    feats.append(trap_prob)
    premium  = 1.0 if (mm_data and mm_data.get("premium_zone"))  else 0.0
    discount = 1.0 if (mm_data and mm_data.get("discount_zone")) else 0.0
    feats.append(premium - discount)   # +1 premium, -1 discount, 0 equilibrium
    feats.append(s(mm_data, "sweep_strength"))

    # ── Pressure Meter (3 features) ───────────────────────────────────────────
    pressure_val = _safe(pressure_data.get("pressure", 0) if pressure_data else 0)
    feats.append(np.clip(pressure_val / 100.0, -1, 1))
    feats.append(1.0 if (pressure_data and pressure_data.get("filter_passed")) else 0.0)
    p_dir_map = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}
    feats.append(p_dir_map.get(pressure_data.get("direction", "NEUTRAL") if pressure_data else "NEUTRAL", 0.0))

    # ── Rare Setup (3 features) ───────────────────────────────────────────────
    feats.append(1.0 if (rare_data and rare_data.get("rare_setup")) else 0.0)
    feats.append(s(rare_data, "score"))
    feats.append(_safe(rare_data.get("signal_bonus", 0) if rare_data else 0) / 100.0)

    # ── Geopolitical (4 features) ─────────────────────────────────────────────
    feats.append(s(geo_data, "geo_score"))
    geo_bias_map = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}
    feats.append(geo_bias_map.get(geo_data.get("geo_bias", "NEUTRAL") if geo_data else "NEUTRAL", 0.0))
    feats.append(s(geo_data, "geo_confidence"))
    feats.append(1.0 if (geo_data and geo_data.get("rare_macro_setup")) else 0.0)

    # ── Bitcoin Cycle (5 features) ────────────────────────────────────────────
    feats.append(s(cycle_data, "cycle_score"))
    c_mult = _safe(cycle_data.get("risk_multiplier", 1.0) if cycle_data else 1.0)
    feats.append(np.clip(c_mult / 2.0, 0, 1))  # 0.5x→0.25, 1.0x→0.5, 2.0x→1.0
    cycle_phase_scores = {
        "ACCUMULATION": 0.6, "EARLY_BULL": 0.8, "MID_BULL": 0.9,
        "LATE_BULL": 0.7, "DISTRIBUTION": 0.4, "BEAR": 0.2, "CAPITULATION": 0.1,
    }
    c_phase = cycle_data.get("cycle_phase", "ACCUMULATION") if cycle_data else "ACCUMULATION"
    feats.append(cycle_phase_scores.get(c_phase, 0.5))
    feats.append(1.0 if (cycle_data and cycle_data.get("rare_cycle_setup")) else 0.0)
    feats.append(s(cycle_data, "cycle_confidence"))

    # ── Institutional Direction (6 features) ──────────────────────────────────
    feats.append(s(direction_data, "direction_score"))
    d_dir_map = {"UP": 1.0, "DOWN": -1.0, "NEUTRAL": 0.0}
    feats.append(d_dir_map.get(direction_data.get("direction", "NEUTRAL") if direction_data else "NEUTRAL", 0.0))
    feats.append(s(direction_data, "direction_confidence"))
    feats.append(_safe(direction_data.get("fake_move_probability", 30) if direction_data else 30) / 100.0)
    feats.append(_safe(direction_data.get("breakout_probability", 30) if direction_data else 30) / 100.0)
    feats.append(1.0 if (direction_data and direction_data.get("rare_direction_setup")) else 0.0)

    # ── Regime / Volatilidade (4 features) ────────────────────────────────────
    regime_score_map = {
        "TENDÊNCIA_FORTE": 0.9, "TENDÊNCIA": 0.7, "RANGING": 0.5,
        "VOLATIL": 0.6, "COMPRESSÃO": 0.4, "NEUTRO": 0.5,
    }
    r_regime = regime_data.get("regime", "NEUTRO") if regime_data else "NEUTRO"
    feats.append(regime_score_map.get(r_regime, 0.5))
    feats.append(np.clip(_safe(regime_data.get("atr_pct", 1.0) if regime_data else 1.0) / 5.0, 0, 1))
    feats.append(1.0 if (regime_data and regime_data.get("squeeze")) else 0.0)
    adx_norm = np.clip(_safe(regime_data.get("adx", 20) if regime_data else 20) / 60.0, 0, 1)
    feats.append(adx_norm)

    # ── Volume (3 features) ───────────────────────────────────────────────────
    feats.append(1.0 if (volume_data and volume_data.get("high_volume")) else 0.0)
    feats.append(1.0 if (volume_data and volume_data.get("cvd_trending_up")) else 0.0)
    feats.append(1.0 if (volume_data and volume_data.get("trap_signal")) else 0.0)

    # ── Derivativos (3 features) ──────────────────────────────────────────────
    funding = _safe(deriv_data.get("funding_rate", 0.01) if deriv_data else 0.01) * 100
    feats.append(np.clip(funding / 0.1, -1, 1))  # normaliza funding rate
    long_pct = _safe(deriv_data.get("long_pct", 50) if deriv_data else 50)
    feats.append((long_pct - 50) / 50.0)  # -1 a +1
    feats.append(np.clip(_safe(trend_data.get("score", 50) if trend_data else 50) / 100.0, 0, 1))

    # ── Total esperado ────────────────────────────────────────────────────────
    # 6+4+5+3+3+4+5+6+4+3+3 = 46 → pad até TABULAR_DIM
    result = np.array(feats, dtype=np.float32)

    if len(result) < TABULAR_DIM:
        result = np.concatenate([result, np.zeros(TABULAR_DIM - len(result), dtype=np.float32)])
    elif len(result) > TABULAR_DIM:
        result = result[:TABULAR_DIM]

    # Substituir NaN/Inf por 0
    result = np.where(np.isfinite(result), result, 0.0)
    return result.astype(np.float32)


# ─── Construção da matriz MTF ──────────────────────────────────────────────────
def _build_mtf_matrix(smc_mtf_data: Optional[Dict]) -> np.ndarray:
    """
    Monta matriz [MTF_TIMEFRAMES, FEATURE_DIM] para o Transformer.
    Usa dados do SmartMoneyEngine MTF analysis (1D, 4H, 1H, 15m).
    """
    tf_keys    = ["1d", "4h", "1h", "15m"]
    mtf_matrix = np.zeros((MTF_TIMEFRAMES, FEATURE_DIM), dtype=np.float32)

    if not smc_mtf_data:
        return mtf_matrix

    mtf_scores = smc_mtf_data.get("timeframe_scores", {})

    for i, tf in enumerate(tf_keys):
        tf_data = mtf_scores.get(tf, {})
        if not tf_data:
            continue
        row = np.zeros(FEATURE_DIM, dtype=np.float32)
        # Features por timeframe (primeiras 8 colunas)
        row[0] = _safe(tf_data.get("smc_score",         50)) / 100.0
        row[1] = _safe(tf_data.get("structure_score",   50)) / 100.0
        row[2] = _safe(tf_data.get("liquidity_score",   50)) / 100.0
        row[3] = _safe(tf_data.get("bos_score",         50)) / 100.0
        row[4] = _safe(tf_data.get("choch_score",       50)) / 100.0
        row[5] = _safe(tf_data.get("order_block_score", 50)) / 100.0
        row[6] = _safe(tf_data.get("fvg_score",         50)) / 100.0
        row[7] = _safe(tf_data.get("momentum_score",    50)) / 100.0
        mtf_matrix[i] = row

    return mtf_matrix


# ─── Entry point público ───────────────────────────────────────────────────────
def extract_features(
    df:             pd.DataFrame,
    price:          float,
    inst_data:      Optional[Dict] = None,
    smc_data:       Optional[Dict] = None,
    mm_data:        Optional[Dict] = None,
    pressure_data:  Optional[Dict] = None,
    rare_data:      Optional[Dict] = None,
    geo_data:       Optional[Dict] = None,
    cycle_data:     Optional[Dict] = None,
    direction_data: Optional[Dict] = None,
    regime_data:    Optional[Dict] = None,
    volume_data:    Optional[Dict] = None,
    trend_data:     Optional[Dict] = None,
    deriv_data:     Optional[Dict] = None,
    smc_mtf_data:   Optional[Dict] = None,
) -> FeatureSet:
    """
    Ponto de entrada principal do Feature Engine.
    Recebe todos os outputs dos motores Trinity e retorna FeatureSet normalizado.
    """
    try:
        # Defaults seguros para dicts None
        inst_data      = inst_data      or {}
        regime_data    = regime_data    or {}
        volume_data    = volume_data    or {}
        trend_data     = trend_data     or {}
        deriv_data     = deriv_data     or {}

        # Vetor sequencial (LSTM, CNN)
        sequence = _extract_ohlcv_sequence(df)

        # Vetor tabular (MLP)
        tabular = _extract_tabular_features(
            price=price,
            inst_data=inst_data,
            smc_data=smc_data,
            mm_data=mm_data,
            pressure_data=pressure_data,
            rare_data=rare_data,
            geo_data=geo_data,
            cycle_data=cycle_data,
            direction_data=direction_data,
            regime_data=regime_data,
            volume_data=volume_data,
            trend_data=trend_data,
            deriv_data=deriv_data,
        )

        # Matriz multi-timeframe (Transformer)
        mtf_matrix = _build_mtf_matrix(smc_mtf_data)

        # Scalars brutos para uso em outros módulos
        raw_scalars = {
            "inst_score":       _safe(inst_data.get("inst_score", 50)),
            "direction_score":  _safe(direction_data.get("direction_score", 50) if direction_data else 50),
            "cycle_score":      _safe(cycle_data.get("cycle_score", 50) if cycle_data else 50),
            "geo_score":        _safe(geo_data.get("geo_score", 50) if geo_data else 50),
            "smc_score":        _safe(smc_data.get("smc_score", 50) if smc_data else 50),
            "mm_score":         _safe(mm_data.get("market_maker_score", 50) if mm_data else 50),
            "pressure":         _safe(pressure_data.get("pressure", 0) if pressure_data else 0),
            "price":            _safe(price),
        }

        return FeatureSet(
            sequence=sequence,
            tabular=tabular,
            mtf_matrix=mtf_matrix,
            raw_scalars=raw_scalars,
            valid=True,
        )

    except Exception as e:
        log.error(f"[FeatureEngine] Erro na extração de features: {e}", exc_info=True)
        return FeatureSet(
            sequence=np.zeros((SEQUENCE_LEN, FEATURE_DIM), dtype=np.float32),
            tabular=np.zeros(TABULAR_DIM, dtype=np.float32),
            mtf_matrix=np.zeros((MTF_TIMEFRAMES, FEATURE_DIM), dtype=np.float32),
            valid=False,
            error=str(e),
        )
