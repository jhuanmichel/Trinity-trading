"""
market_maker_engine.py — Market Maker Positioning Detection
Trinity Trading | Institutional Direction Engine

Detecta:
- Zonas de rejeição repetida (price defense levels)
- Absorção institucional em níveis-chave
- Liquidez protegida por market makers
- Intenção direcional via padrões de defesa

Lógica central:
  3+ rejeições no mesmo nível → market maker defendendo
  Força da defesa → volume, tamanho do wick, recência, velocidade
  Direção provável → tipo de zona + funding rate + OI bias
"""

import logging
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ─── Configuração ─────────────────────────────────────────────────────────────

REJECTION_TOLERANCE_PCT = 0.004   # 0.4% — clustering de níveis próximos
MIN_REJECTIONS          = 2       # mínimo de rejeições para flagear defesa
WICK_RATIO_THRESHOLD    = 0.35    # wick/range mínimo para contar como rejeição
PROXIMITY_PCT           = 0.010   # 1.0% — distância máxima ao nível ativo
LOOKBACK_4H             = 60      # candles 4H a analisar
LOOKBACK_1H             = 100     # candles 1H a analisar


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _wick_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adiciona métricas de wick ao DataFrame.

    upper_wick       = High  - max(Open, Close)
    lower_wick       = min(Open, Close) - Low
    upper_wick_ratio = upper_wick / range    (0-1)
    lower_wick_ratio = lower_wick / range    (0-1)
    body_ratio       = |Close - Open| / range
    """
    df = df.copy()
    body_top    = df[["Open", "Close"]].max(axis=1)
    body_bottom = df[["Open", "Close"]].min(axis=1)
    candle_range = (df["High"] - df["Low"]).replace(0, np.nan)

    df["upper_wick"]       = df["High"]  - body_top
    df["lower_wick"]       = body_bottom - df["Low"]
    df["candle_range"]     = candle_range
    df["upper_wick_ratio"] = (df["upper_wick"] / candle_range).fillna(0)
    df["lower_wick_ratio"] = (df["lower_wick"] / candle_range).fillna(0)
    df["body_ratio"]       = (abs(df["Close"] - df["Open"]) / candle_range).fillna(0)
    return df


def _find_rejection_clusters(df: pd.DataFrame,
                              tolerance_pct: float = REJECTION_TOLERANCE_PCT) -> list:
    """
    Encontra clusters de rejeição no DataFrame OHLCV.

    Rejeição de RESISTÊNCIA: High ≥ nível, mas Close < nível (wick superior longo)
    Rejeição de SUPORTE:     Low  ≤ nível, mas Close > nível (wick inferior longo)

    Retorna lista de clusters:
      {level, type, count, avg_volume, avg_wick_ratio, recency_count}
    """
    df = _wick_analysis(df)
    df = df.reset_index(drop=True)
    n  = len(df)

    # Rejeições bearish: upper_wick_ratio >= threshold → nivel = High
    bearish = df[df["upper_wick_ratio"] >= WICK_RATIO_THRESHOLD].copy()
    bearish["rej_level"] = bearish["High"]
    bearish["rej_type"]  = "RESISTANCE"

    # Rejeições bullish: lower_wick_ratio >= threshold → nivel = Low
    bullish = df[df["lower_wick_ratio"] >= WICK_RATIO_THRESHOLD].copy()
    bullish["rej_level"] = bullish["Low"]
    bullish["rej_type"]  = "SUPPORT"

    all_rej = pd.concat([bearish, bullish], ignore_index=False)
    if all_rej.empty:
        return []

    used   = set()
    result = []

    for idx, row in all_rej.iterrows():
        if idx in used:
            continue

        level    = row["rej_level"]
        rej_type = row["rej_type"]
        tol      = level * tolerance_pct
        wick_col = "upper_wick_ratio" if rej_type == "RESISTANCE" else "lower_wick_ratio"

        # Agrupa todas as rejeições do mesmo tipo dentro da tolerância
        nearby = all_rej[
            (all_rej["rej_type"] == rej_type) &
            (abs(all_rej["rej_level"] - level) <= tol)
        ]

        if len(nearby) < MIN_REJECTIONS:
            continue

        used.update(nearby.index.tolist())

        avg_level  = float(nearby["rej_level"].mean())
        avg_volume = float(nearby["Volume"].mean()) if "Volume" in nearby.columns else 0.0
        avg_wick   = float(nearby[wick_col].mean())

        # Recência: quantas rejeições nas últimas 20 velas?
        recent_cutoff = n - 20
        recency_count = int((nearby.index >= recent_cutoff).sum())

        result.append({
            "level":          round(avg_level, 2),
            "type":           rej_type,
            "count":          len(nearby),
            "avg_volume":     avg_volume,
            "avg_wick_ratio": round(avg_wick, 3),
            "recency_count":  recency_count,
        })

    # Ordena por contagem de rejeições (mais rejeitado = mais importante)
    return sorted(result, key=lambda x: x["count"], reverse=True)


def _score_defense_strength(cluster: dict, vol_baseline: float) -> float:
    """
    Pontua a força da defesa do market maker em um cluster. Retorna 0-100.

    Fatores:
    - Contagem de rejeições      (peso: 30)
    - Qualidade do wick médio    (peso: 25)
    - Volume vs baseline         (peso: 25)
    - Recência das rejeições     (peso: 20)
    """
    score = 0.0

    # Rejeições (max 30)
    n = cluster["count"]
    if   n >= 6: score += 30
    elif n >= 4: score += 22
    elif n >= 3: score += 16
    elif n >= 2: score += 10

    # Qualidade do wick (max 25)
    wr = cluster["avg_wick_ratio"]
    if   wr >= 0.65: score += 25
    elif wr >= 0.50: score += 20
    elif wr >= 0.40: score += 14
    elif wr >= 0.35: score += 8

    # Volume vs baseline (max 25)
    if vol_baseline > 0:
        vr = cluster["avg_volume"] / vol_baseline
        if   vr >= 2.5: score += 25
        elif vr >= 1.8: score += 20
        elif vr >= 1.3: score += 14
        elif vr >= 1.0: score += 8

    # Recência (max 20)
    rc = cluster["recency_count"]
    if   rc >= 4: score += 20
    elif rc >= 3: score += 15
    elif rc >= 2: score += 10
    elif rc >= 1: score += 6

    return round(min(100.0, score), 1)


def _likely_direction(cluster: dict, funding_rate: float, oi_bias: str) -> str:
    """
    Determina a direção provável após detectar uma zona de defesa MM.

    SUPORTE  → MM compra a queda         → geralmente UP
    RESISTÊNCIA + funding negativo       → shorts armadilhados → UP (squeeze)
    RESISTÊNCIA + funding positivo       → longs saturados → DOWN
    RESISTÊNCIA + funding neutro         → DOWN (default conservador)
    """
    zone = cluster["type"]

    if zone == "SUPPORT":
        # MM defendendo suporte = comprando dip = bullish
        return "UP"

    # RESISTÊNCIA: depende do posicionamento alavancado
    if funding_rate < -0.00015:
        # Funding negativo = shorts dominam = risco de squeeze
        return "UP"
    if funding_rate > 0.00025:
        # Funding positivo alto = longs excessivos = bearish (armadilha)
        return "DOWN"

    # OI bias como desempate
    if oi_bias == "BULLISH":
        return "UP"

    return "DOWN"  # default conservador em resistência


# ─── Run ─────────────────────────────────────────────────────────────────────

def run_market_maker_engine(
    ohlcv_1h:      list,
    ohlcv_4h:      list,
    current_price: float,
    funding_rate:  float = 0.0,
    oi_bias:       str   = "NEUTRAL",
) -> dict:
    """
    Engine de Posicionamento de Market Maker.

    Args:
        ohlcv_1h:      [[ts, open, high, low, close, volume], ...]
        ohlcv_4h:      [[ts, open, high, low, close, volume], ...]
        current_price: preço atual do BTC
        funding_rate:  taxa de funding atual (float, ex: 0.0001)
        oi_bias:       "BULLISH" | "BEARISH" | "NEUTRAL"

    Returns:
        {
            "market_maker_defense": bool,
            "defense_level":        float | None,
            "defense_type":         "RESISTANCE" | "SUPPORT" | None,
            "defense_strength":     float 0-100,
            "likely_direction":     "UP" | "DOWN" | "NEUTRAL",
            "mm_score":             float 0-100,
            "rejections_found":     int,
            "all_levels":           list,
        }
    """
    _neutral = {
        "market_maker_defense": False,
        "defense_level":        None,
        "defense_type":         None,
        "defense_strength":     0.0,
        "likely_direction":     "NEUTRAL",
        "mm_score":             50.0,
        "rejections_found":     0,
        "all_levels":           [],
    }

    try:
        cols = ["Open", "High", "Low", "Close", "Volume"]

        df_1h = pd.DataFrame(ohlcv_1h, columns=["ts"] + cols).astype(float).drop("ts", axis=1)
        df_4h = pd.DataFrame(ohlcv_4h, columns=["ts"] + cols).astype(float).drop("ts", axis=1)

        df_1h = df_1h.tail(LOOKBACK_1H).reset_index(drop=True)
        df_4h = df_4h.tail(LOOKBACK_4H).reset_index(drop=True)

        if len(df_1h) < 10 or len(df_4h) < 5:
            return _neutral

        vol_baseline_1h = float(df_1h["Volume"].mean()) or 1.0
        vol_baseline_4h = float(df_4h["Volume"].mean()) or 1.0

        # Encontra clusters em 1H e 4H
        clusters_1h = _find_rejection_clusters(df_1h)
        clusters_4h = _find_rejection_clusters(df_4h)

        all_clusters = []

        for c in clusters_4h:
            c["timeframe"]       = "4H"
            c["tf_weight"]       = 1.4   # 4H tem mais peso institucional
            c["defense_strength"] = _score_defense_strength(c, vol_baseline_4h)
            all_clusters.append(c)

        for c in clusters_1h:
            c["timeframe"]       = "1H"
            c["tf_weight"]       = 1.0
            c["defense_strength"] = _score_defense_strength(c, vol_baseline_1h)
            all_clusters.append(c)

        if not all_clusters:
            return _neutral

        # Calcula distância ao preço atual
        for c in all_clusters:
            c["distance_pct"] = abs(c["level"] - current_price) / current_price

        # Top 5 níveis para contexto
        top5 = sorted(all_clusters, key=lambda x: x["defense_strength"] * x["tf_weight"], reverse=True)[:5]
        all_levels_fmt = [
            {
                "level":    c["level"],
                "type":     c["type"],
                "strength": c["defense_strength"],
                "tf":       c["timeframe"],
                "count":    c["count"],
            }
            for c in top5
        ]

        # Filtra zonas próximas ao preço atual
        nearby = [c for c in all_clusters if c["distance_pct"] <= PROXIMITY_PCT]

        if not nearby:
            # Sem zona próxima — retorna contexto sem defesa ativa
            _neutral["all_levels"] = all_levels_fmt
            return _neutral

        # Melhor zona próxima (força × peso do timeframe)
        best = max(nearby, key=lambda x: x["defense_strength"] * x["tf_weight"])

        direction = _likely_direction(best, funding_rate, oi_bias)
        strength  = best["defense_strength"]

        # mm_score: >50 = bullish, <50 = bearish
        if direction == "UP":
            mm_score = round(50.0 + strength * 0.45, 1)
        elif direction == "DOWN":
            mm_score = round(50.0 - strength * 0.45, 1)
        else:
            mm_score = 50.0

        mm_score = round(min(100.0, max(0.0, mm_score)), 1)

        return {
            "market_maker_defense": True,
            "defense_level":        best["level"],
            "defense_type":         best["type"],
            "defense_strength":     strength,
            "likely_direction":     direction,
            "mm_score":             mm_score,
            "rejections_found":     best["count"],
            "defense_timeframe":    best["timeframe"],
            "all_levels":           all_levels_fmt,
        }

    except Exception as e:
        log.warning(f"   MM Engine erro: {e}", exc_info=False)
        return _neutral
