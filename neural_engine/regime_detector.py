"""
regime_detector.py — Detector de Regime de Mercado
Cap. 17 | Trinity Neural Intelligence Engine v2

Detecta o regime atual com base em regras estatísticas + outputs Trinity.
Operação 100% determinística — sem dependência de PyTorch.

Regimes detectados:
  TRENDING            → tendência direcional clara (alta ou baixa)
  RANGING             → mercado lateral / consolidação
  REVERSAL            → reversão iminente / sinais de mudança
  ACCUMULATION        → fase de acumulação institucional (whale buying)
  DISTRIBUTION        → fase de distribuição (venda institucional)
  VOLATILITY_EXPANSION → expansão de volatilidade / breakout iminente
  MANIPULATION        → movimento artificial / armadilha de mercado

Score de confiança: 0–100
Peso na ensemble: 0.15 (conforme spec)
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Dict, Any

from .feature_engine import FeatureSet, _safe

log = logging.getLogger(__name__)

# ─── Regimes ──────────────────────────────────────────────────────────────────
REGIME_TRENDING             = "TRENDING"
REGIME_RANGING              = "RANGING"
REGIME_REVERSAL             = "REVERSAL"
REGIME_ACCUMULATION         = "ACCUMULATION"
REGIME_DISTRIBUTION         = "DISTRIBUTION"
REGIME_VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
REGIME_MANIPULATION         = "MANIPULATION"

ALL_REGIMES = [
    REGIME_TRENDING, REGIME_RANGING, REGIME_REVERSAL,
    REGIME_ACCUMULATION, REGIME_DISTRIBUTION,
    REGIME_VOLATILITY_EXPANSION, REGIME_MANIPULATION,
]


# ─── Saída ─────────────────────────────────────────────────────────────────────
@dataclass
class RegimeResult:
    regime:      str    # regime dominante
    confidence:  float  # 0-100
    sub_regimes: Dict[str, float]  # score de cada regime (0-100)
    # Probabilidades normalizadas para a ensemble (soma = 1.0)
    probs: Dict[str, float]

    def to_neural_output(self) -> Dict[str, float]:
        """Mapeia regime para as probabilidades do output neural padrão."""
        bull = self.probs.get(REGIME_TRENDING, 0) * 0.6 + self.probs.get(REGIME_ACCUMULATION, 0) * 0.8
        bear = self.probs.get(REGIME_TRENDING, 0) * 0.4 + self.probs.get(REGIME_DISTRIBUTION, 0) * 0.7
        side = self.probs.get(REGIME_RANGING, 0)  * 0.9 + self.probs.get(REGIME_ACCUMULATION, 0) * 0.2
        vexp = self.probs.get(REGIME_VOLATILITY_EXPANSION, 0)
        manip = self.probs.get(REGIME_MANIPULATION, 0)
        rev  = self.probs.get(REGIME_REVERSAL, 0)
        # Redistribui reversão proporcionalmente
        bull += rev * 0.5
        bear += rev * 0.5
        return {
            "bull_probability":                 round(min(bull * 100, 100), 1),
            "bear_probability":                 round(min(bear * 100, 100), 1),
            "sideways_probability":             round(min(side * 100, 100), 1),
            "volatility_expansion_probability": round(min(vexp * 100, 100), 1),
            "manipulation_probability":         round(min(manip * 100, 100), 1),
            "confidence_score":                 round(self.confidence, 1),
            "market_regime":                    self.regime,
        }


# ─── Detector ─────────────────────────────────────────────────────────────────
class RegimeDetector:
    """
    Detector de regime de mercado multi-camadas.
    Combina análise estatística de OHLCV com scores dos motores Trinity.
    """

    def __init__(self):
        self._last_regime = REGIME_RANGING
        self._regime_history: list = []

    def detect(
        self,
        df:             pd.DataFrame,
        feature_set:    FeatureSet,
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
    ) -> RegimeResult:
        """
        Executa detecção completa de regime.
        Retorna RegimeResult com regime dominante, confiança e distribuição de probabilidades.
        """
        try:
            scores = {}

            scores[REGIME_TRENDING]             = self._score_trending(df, regime_data, trend_data)
            scores[REGIME_RANGING]              = self._score_ranging(df, regime_data, volume_data)
            scores[REGIME_REVERSAL]             = self._score_reversal(df, smc_data, mm_data, volume_data)
            scores[REGIME_ACCUMULATION]         = self._score_accumulation(df, volume_data, direction_data, cycle_data, inst_data)
            scores[REGIME_DISTRIBUTION]         = self._score_distribution(df, volume_data, direction_data, cycle_data)
            scores[REGIME_VOLATILITY_EXPANSION] = self._score_vol_expansion(df, regime_data, rare_data)
            scores[REGIME_MANIPULATION]         = self._score_manipulation(mm_data, rare_data, direction_data)

            # Regime dominante
            dominant = max(scores, key=scores.get)
            dom_score = scores[dominant]

            # Confiança: spread entre 1º e 2º melhor
            sorted_scores = sorted(scores.values(), reverse=True)
            spread = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 0
            confidence = min(30.0 + spread * 0.7, 99.0)

            # Normaliza para probabilidades
            total = sum(scores.values()) + 1e-8
            probs = {r: v / total for r, v in scores.items()}

            # Suavização temporal (evita flip de regime a cada ciclo)
            final_regime = self._smooth_regime(dominant, confidence)

            result = RegimeResult(
                regime=final_regime,
                confidence=round(confidence, 1),
                sub_regimes={r: round(v, 1) for r, v in scores.items()},
                probs=probs,
            )

            self._last_regime = final_regime
            self._regime_history.append(final_regime)
            if len(self._regime_history) > 20:
                self._regime_history.pop(0)

            return result

        except Exception as e:
            log.error(f"[RegimeDetector] Erro: {e}", exc_info=True)
            return self._neutral_result()

    # ─── Scorers por regime ───────────────────────────────────────────────────

    def _score_trending(self, df, regime_data, trend_data) -> float:
        """TRENDING: tendência direcional forte com volume e momentum."""
        score = 0.0

        # ADX > 25 indica tendência
        adx = _safe(regime_data.get("adx", 20) if regime_data else 20)
        if adx >= 40: score += 35
        elif adx >= 25: score += 20
        elif adx >= 18: score += 8

        # EMA alinhado
        ema_sig = trend_data.get("ema_signal", "") if trend_data else ""
        if "BULL" in str(ema_sig).upper() or "BEAR" in str(ema_sig).upper():
            score += 20

        # ATR% indica movimento real
        atr_pct = _safe(regime_data.get("atr_pct", 1.0) if regime_data else 1.0)
        if atr_pct >= 2.0: score += 15
        elif atr_pct >= 1.0: score += 8

        # Análise de slope da sequência
        if len(df) >= 20:
            closes = df["close"].values[-20:]
            slope = np.polyfit(np.arange(20), closes, 1)[0]
            slope_pct = abs(slope) / (closes.mean() + 1e-8) * 100
            if slope_pct >= 0.3: score += 15
            elif slope_pct >= 0.1: score += 8

        # Ichimoku
        if trend_data:
            if trend_data.get("ichimoku_above_cloud") or trend_data.get("ichimoku_below_cloud"):
                score += 15

        return min(score, 100.0)

    def _score_ranging(self, df, regime_data, volume_data) -> float:
        """RANGING: mercado lateral / consolidação."""
        score = 0.0

        # ADX baixo
        adx = _safe(regime_data.get("adx", 20) if regime_data else 20)
        if adx < 15: score += 35
        elif adx < 20: score += 20
        elif adx < 25: score += 10

        # Squeeze de Bollinger (regime comprimido)
        if regime_data and regime_data.get("squeeze"):
            score += 20

        # Volume baixo = consolidação
        if volume_data and not volume_data.get("high_volume"):
            score += 15

        # Análise de range das últimas 20 barras
        if len(df) >= 20:
            closes  = df["close"].values[-20:]
            highs   = df["high"].values[-20:]
            lows    = df["low"].values[-20:]
            rng     = (highs.max() - lows.min()) / (closes.mean() + 1e-8) * 100
            if rng < 2.0: score += 30
            elif rng < 4.0: score += 15

        return min(score, 100.0)

    def _score_reversal(self, df, smc_data, mm_data, volume_data) -> float:
        """REVERSAL: sinais de inversão de tendência."""
        score = 0.0

        # CHOCH (Change of Character) = sinal forte de reversão
        if smc_data:
            structure = smc_data.get("structure", {})
            if isinstance(structure, dict) and structure.get("choch"):
                score += 35
            if isinstance(structure, dict) and (structure.get("bos_bull") or structure.get("bos_bear")):
                score += 15

        # Sweep de liquidez
        if smc_data:
            structure = smc_data.get("structure", {})
            if isinstance(structure, dict) and (structure.get("sweep_low") or structure.get("sweep_high")):
                score += 20

        # MM trap de alta probabilidade
        if mm_data:
            trap_prob = _safe(mm_data.get("trap_probability", 0))
            if trap_prob >= 70: score += 25
            elif trap_prob >= 50: score += 15

        # Volume com divergência
        if volume_data and volume_data.get("trap_signal"):
            score += 20

        # Divergência RSI/Preço (análise direta no df)
        if len(df) >= 30:
            closes = df["close"].values[-30:]
            gains  = np.maximum(np.diff(closes), 0)
            losses = np.maximum(-np.diff(closes), 0)
            ag = pd.Series(gains).rolling(14).mean().values[-1] + 1e-8
            al = pd.Series(losses).rolling(14).mean().values[-1] + 1e-8
            rsi = 100 - 100 / (1 + ag / al)
            # Divergência: preço faz HH mas RSI não
            price_hh = closes[-1] > closes[-15]
            rsi_hh   = rsi > 70
            if price_hh and not rsi_hh:
                score += 15  # divergência bearish
            elif not price_hh and rsi < 30:
                score += 15  # divergência bullish

        return min(score, 100.0)

    def _score_accumulation(self, df, volume_data, direction_data, cycle_data, inst_data) -> float:
        """ACCUMULATION: fase de compras institucionais silenciosas."""
        score = 0.0

        # Fase de ciclo Bitcoin
        if cycle_data:
            phase = cycle_data.get("cycle_phase", "")
            if phase in ("ACCUMULATION", "EARLY_BULL"): score += 30

        # Direção = compras líquidas
        if direction_data:
            if direction_data.get("direction") == "UP": score += 20
            d_ibias = direction_data.get("institutional_bias", "NEUTRAL")
            if d_ibias in ("BULLISH", "ACCUMULATION"): score += 20
            abs_type = direction_data.get("absorption_type")
            if abs_type == "BUY_ABSORPTION": score += 15

        # Volume crescente em fundo (modo silencioso)
        if volume_data and volume_data.get("cvd_trending_up"):
            score += 15

        # Score institucional alto
        if inst_data:
            inst_score = _safe(inst_data.get("inst_score", 50))
            if inst_score >= 70: score += 10
            if inst_data.get("direction") == "LONG": score += 10

        return min(score, 100.0)

    def _score_distribution(self, df, volume_data, direction_data, cycle_data) -> float:
        """DISTRIBUTION: fase de vendas institucionais silenciosas."""
        score = 0.0

        # Fase de ciclo Bitcoin
        if cycle_data:
            phase = cycle_data.get("cycle_phase", "")
            if phase in ("DISTRIBUTION", "LATE_BULL"): score += 30

        # Direção = vendas líquidas
        if direction_data:
            if direction_data.get("direction") == "DOWN": score += 20
            d_ibias = direction_data.get("institutional_bias", "NEUTRAL")
            if d_ibias in ("BEARISH", "DISTRIBUTION"): score += 20
            abs_type = direction_data.get("absorption_type")
            if abs_type == "SELL_ABSORPTION": score += 15

        # Volume cadente com preço alto
        if volume_data and not volume_data.get("cvd_trending_up"):
            score += 10

        # Análise: preço em máxima com volume decrescente
        if len(df) >= 20:
            closes  = df["close"].values[-20:]
            volumes = df["volume"].values[-20:]
            price_trend = np.polyfit(np.arange(20), closes, 1)[0]
            vol_trend   = np.polyfit(np.arange(20), volumes, 1)[0]
            if price_trend > 0 and vol_trend < 0:
                score += 20  # topo com volume caindo = distribuição

        return min(score, 100.0)

    def _score_vol_expansion(self, df, regime_data, rare_data) -> float:
        """VOLATILITY_EXPANSION: expansão de volatilidade / breakout iminente."""
        score = 0.0

        # Squeeze + rompimento
        if regime_data and regime_data.get("squeeze"):
            score += 25

        # ATR% elevado
        atr_pct = _safe(regime_data.get("atr_pct", 1.0) if regime_data else 1.0)
        if atr_pct >= 3.0: score += 30
        elif atr_pct >= 2.0: score += 15

        # Rare setup de volatilidade
        if rare_data:
            setup = rare_data.get("setup_type", "")
            if "COMPRESSÃO" in str(setup).upper() or "VOLATIL" in str(setup).upper():
                score += 30
            if rare_data.get("rare_setup"):
                score += 15

        # Análise: variância recente vs histórica
        if len(df) >= 30:
            closes   = df["close"].values
            ret      = np.diff(np.log(closes + 1e-8))
            vol_5    = ret[-5:].std() if len(ret) >= 5 else 0
            vol_20   = ret[-20:].std() if len(ret) >= 20 else 0.01
            vol_ratio = vol_5 / (vol_20 + 1e-8)
            if vol_ratio >= 2.0: score += 20
            elif vol_ratio >= 1.5: score += 10

        return min(score, 100.0)

    def _score_manipulation(self, mm_data, rare_data, direction_data) -> float:
        """MANIPULATION: movimento artificial / stop hunt / armadilha."""
        score = 0.0

        # Market Maker trap de alta probabilidade
        if mm_data:
            trap_prob = _safe(mm_data.get("trap_probability", 0))
            if trap_prob >= 75: score += 40
            elif trap_prob >= 60: score += 25
            elif trap_prob >= 45: score += 12

            trap_dir = mm_data.get("trap_direction", "NEUTRAL")
            if trap_dir not in ("NEUTRAL", "NEUTRO", ""):
                score += 10

        # Fake move de alta probabilidade
        if direction_data:
            fake_prob = _safe(direction_data.get("fake_move_probability", 30))
            if fake_prob >= 65: score += 30
            elif fake_prob >= 50: score += 15

        # Rare setup tipo manipulação
        if rare_data and rare_data.get("rare_setup"):
            factors = rare_data.get("factors_active", [])
            manip_factors = [f for f in factors if any(
                k in str(f).upper() for k in ("TRAP", "FAKE", "HUNT", "SWEEP", "STOP")
            )]
            score += len(manip_factors) * 15

        return min(score, 100.0)

    # ─── Suavização temporal ──────────────────────────────────────────────────
    def _smooth_regime(self, new_regime: str, confidence: float) -> str:
        """
        Evita flip de regime com alta frequência.
        Requer confiança >= 55 para mudar regime com menos de 3 ciclos consecutivos.
        """
        if not self._regime_history:
            return new_regime

        # Conta frequência nos últimos 5 regimes
        recent = self._regime_history[-5:] if len(self._regime_history) >= 5 else self._regime_history
        dominant_recent = max(set(recent), key=recent.count) if recent else new_regime
        recent_count = recent.count(new_regime)

        # Muda regime se: confiança alta OU detectado >= 2 vezes recentes
        if confidence >= 65 or recent_count >= 2 or new_regime == dominant_recent:
            return new_regime
        return self._last_regime

    def _neutral_result(self) -> RegimeResult:
        uniform = 1.0 / len(ALL_REGIMES)
        return RegimeResult(
            regime=REGIME_RANGING,
            confidence=30.0,
            sub_regimes={r: 14.0 for r in ALL_REGIMES},
            probs={r: uniform for r in ALL_REGIMES},
        )
