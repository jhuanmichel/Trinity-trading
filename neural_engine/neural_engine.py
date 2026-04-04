"""
neural_engine.py — Trinity Neural Intelligence Engine v2 (Orquestrador)
Cap. 17 | Sistema neural multi-modelo adaptativo para inteligência institucional.

Fluxo completo:
  1. FeatureEngine  → extrai e normaliza features de todos os motores Trinity
  2. RegimeDetector → detecta regime de mercado (estatístico)
  3. LSTM           → padrões temporais na sequência de preço
  4. Transformer    → inteligência multi-timeframe
  5. CNN            → padrões gráficos (1D convolution)
  6. MLP            → fusão do Trinity Score tabular
  7. EnsembleModel  → combina com pesos LSTM×0.25 + Transformer×0.25 +
                       CNN×0.20 + MLP×0.15 + Regime×0.15
  8. BiasBalancer   → determina viés market-neutral (LONG/SHORT/NEUTRAL)
  9. Rare Neural Setups → detecta 3 setups neurais raros

Output padrão:
  {
    "bull_probability":                 0-100,
    "bear_probability":                 0-100,
    "sideways_probability":             0-100,
    "volatility_expansion_probability": 0-100,
    "manipulation_probability":         0-100,
    "confidence_score":                 0-100,
    "market_regime":                    str,
    "neural_score":                     0-100,
    "neural_bias":                      LONG|SHORT|NEUTRAL,
    "bias_strength":                    0-100,
    "rare_neural_setup":                bool,
    "rare_neural_type":                 str|None,
    "rare_neural_strength":             0-100,
    ...metadados de ensemble e calibração...
  }

Cache: 300 segundos (mesmo padrão dos outros motores).
"""

import logging
import time
from datetime import datetime
from typing import Dict, Optional, Any

import numpy as np
import pandas as pd

from .feature_engine  import extract_features, FeatureSet
from .regime_detector import RegimeDetector, REGIME_MANIPULATION, REGIME_VOLATILITY_EXPANSION
from .lstm_model      import LSTMModel
from .transformer_model import TransformerModel
from .cnn_model       import CNNModel
from .mlp_model       import MLPModel
from .ensemble_model  import EnsembleModel
from .bias_balancer   import BiasBalancer
from .retraining_pipeline import RetrainingPipeline

log = logging.getLogger(__name__)

# ─── Cache ────────────────────────────────────────────────────────────────────
_cache: Dict[str, Any] = {}
CACHE_TTL = 300  # segundos


# ─── Setups neurais raros ─────────────────────────────────────────────────────
RARE_NEURAL_SETUPS = {
    "ACCUMULATION_WHALE_BUYING": {
        "desc":  "Acumulação + Compra de Whales: regime ACCUMULATION com bull ≥ 65% e CVD positivo",
        "emoji": "🐋",
    },
    "DISTRIBUTION_ABSORPTION": {
        "desc":  "Distribuição + Absorção: regime DISTRIBUTION com bear ≥ 65% e volume anormal",
        "emoji": "🕳️",
    },
    "COMPRESSION_VOL_EXPANSION": {
        "desc":  "Compressão + Expansão de Volatilidade: sideways ≥ 60% evoluindo para vol_exp ≥ 30%",
        "emoji": "💥",
    },
}


# ─── Motor Principal ──────────────────────────────────────────────────────────
class NeuralEngine:
    """
    Orquestrador do Trinity Neural Intelligence Engine v2.
    Singleton — inicializado uma vez, reutilizado em todos os ciclos de análise.
    """

    def __init__(self):
        log.info("[NeuralEngine] Inicializando Trinity Neural Intelligence v2...")
        self.regime_detector = RegimeDetector()
        self.lstm_model      = LSTMModel()
        self.transformer     = TransformerModel()
        self.cnn_model       = CNNModel()
        self.mlp_model       = MLPModel()
        self.ensemble        = EnsembleModel()
        self.bias_balancer   = BiasBalancer()

        # Pipeline de retreinamento (inicializa com referência aos modelos)
        self.retrain_pipeline = RetrainingPipeline(
            lstm_model        = self.lstm_model,
            transformer_model = self.transformer,
            cnn_model         = self.cnn_model,
            mlp_model         = self.mlp_model,
        )
        log.info("[NeuralEngine] ✅ Inicialização completa.")

    def analyze(
        self,
        df:             pd.DataFrame,
        price:          float,
        symbol:         str,
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
    ) -> Dict:
        """
        Executa análise neural completa.
        Retorna dicionário com todas as probabilidades e metadados.
        """
        start_ts = time.time()

        try:
            # ── Etapa 1: Extração de Features ──────────────────────────────────
            feature_set = extract_features(
                df=df,
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
                smc_mtf_data=smc_mtf_data,
            )

            if not feature_set.valid:
                log.warning(f"[NeuralEngine] Feature extraction inválida: {feature_set.error}")
                return self._fallback_output(reason=feature_set.error or "feature_invalid")

            # ── Etapa 2: Detecção de Regime ─────────────────────────────────────
            regime_result = self.regime_detector.detect(
                df=df,
                feature_set=feature_set,
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
            regime_neural_out = regime_result.to_neural_output()

            # ── Etapa 3: Inferência dos Modelos Individuais ───────────────────
            lstm_out        = self.lstm_model.predict(feature_set)
            transformer_out = self.transformer.predict(feature_set)
            cnn_out         = self.cnn_model.predict(feature_set)
            mlp_out         = self.mlp_model.predict(feature_set)

            log.debug(
                f"[NeuralEngine] Modelos — "
                f"LSTM B:{lstm_out['bull_probability']:.0f}/Br:{lstm_out['bear_probability']:.0f} | "
                f"TRF B:{transformer_out['bull_probability']:.0f}/Br:{transformer_out['bear_probability']:.0f} | "
                f"CNN B:{cnn_out['bull_probability']:.0f}/Br:{cnn_out['bear_probability']:.0f} | "
                f"MLP B:{mlp_out['bull_probability']:.0f}/Br:{mlp_out['bear_probability']:.0f} | "
                f"Regime:{regime_result.regime}"
            )

            # ── Etapa 4: Ensemble ─────────────────────────────────────────────
            ensemble_out = self.ensemble.combine(
                lstm_output        = lstm_out,
                transformer_output = transformer_out,
                cnn_output         = cnn_out,
                mlp_output         = mlp_out,
                regime_output      = regime_neural_out,
            )

            bull  = ensemble_out["bull_probability"]
            bear  = ensemble_out["bear_probability"]
            side  = ensemble_out["sideways_probability"]
            vexp  = ensemble_out["volatility_expansion_probability"]
            manip = ensemble_out["manipulation_probability"]
            conf  = ensemble_out["confidence_score"]

            # ── Etapa 5: Bias Balancer ────────────────────────────────────────
            bias_result = self.bias_balancer.compute(
                bull_prob=bull, bear_prob=bear, side_prob=side,
                vexp_prob=vexp, manip_prob=manip,
                confidence=conf, regime=regime_result.regime,
            )

            # ── Etapa 6: Setups Neurais Raros ─────────────────────────────────
            rare_neural, rare_type, rare_strength = self._detect_rare_neural_setups(
                bull=bull, bear=bear, side=side, vexp=vexp, manip=manip,
                regime=regime_result.regime,
                volume_data=volume_data,
                direction_data=direction_data,
            )

            # ── Etapa 7: Score Neural Final ────────────────────────────────────
            neural_score = ensemble_out.get("neural_score", 50.0)
            if rare_neural:
                neural_score = min(neural_score * 1.10, 99.0)

            # ── Etapa 8: Log do Sinal para Retreinamento ──────────────────────
            self.retrain_pipeline.log_signal(
                timestamp=datetime.now(),
                price=price,
                neural_bias=bias_result.neural_bias,
                bull_prob=bull,
                bear_prob=bear,
                confidence=conf,
            )

            # ── Retreinamento Condicional ──────────────────────────────────────
            self.retrain_pipeline.label_past_signals(price, datetime.now())
            if self.retrain_pipeline.should_retrain():
                self.retrain_pipeline.trigger_retrain_async()

            # ── Montagem do Output Final ───────────────────────────────────────
            elapsed_ms = round((time.time() - start_ts) * 1000, 1)

            output = {
                # Probabilidades principais
                "bull_probability":                 round(bull, 1),
                "bear_probability":                 round(bear, 1),
                "sideways_probability":             round(side, 1),
                "volatility_expansion_probability": round(vexp, 1),
                "manipulation_probability":         round(manip, 1),
                # Regime e score
                "confidence_score":                 round(conf, 1),
                "market_regime":                    regime_result.regime,
                "neural_score":                     round(neural_score, 1),
                # Viés market-neutral
                "neural_bias":                      bias_result.neural_bias,
                "bias_strength":                    bias_result.bias_strength,
                "bias_confidence":                  bias_result.bias_confidence,
                "bull_edge":                        bias_result.bull_edge,
                "is_balanced":                      bias_result.is_balanced,
                "calibration_note":                 bias_result.calibration_note,
                # Setups raros
                "rare_neural_setup":                rare_neural,
                "rare_neural_type":                 rare_type,
                "rare_neural_strength":             rare_strength,
                # Ensemble metadata
                "agreement_score":                  ensemble_out.get("agreement_score", 50.0),
                "dominant_model":                   ensemble_out.get("dominant_model", "unknown"),
                "model_confidences":                ensemble_out.get("model_confidences", {}),
                "regime_sub_scores":                regime_result.sub_regimes,
                # Performance do retreinamento
                "retrain_accuracy":                 self.retrain_pipeline._get_recent_accuracy(),
                # Metadados
                "elapsed_ms":                       elapsed_ms,
                "timestamp":                        datetime.now().isoformat(),
                "symbol":                           symbol,
            }

            log.info(
                f"[NeuralEngine] ✅ {symbol} | "
                f"Bull:{bull:.0f}% Bear:{bear:.0f}% Side:{side:.0f}% | "
                f"Regime:{regime_result.regime} | Bias:{bias_result.neural_bias} "
                f"({bias_result.bias_strength:.0f}%) | Conf:{conf:.0f}% | "
                f"Score:{neural_score:.0f} | {elapsed_ms}ms"
                + (f" | ★ RARE:{rare_type}" if rare_neural else "")
            )

            return output

        except Exception as e:
            log.error(f"[NeuralEngine] Erro crítico: {e}", exc_info=True)
            return self._fallback_output(reason=str(e))

    # ── Setups Neurais Raros ─────────────────────────────────────────────────

    def _detect_rare_neural_setups(
        self,
        bull: float, bear: float, side: float, vexp: float, manip: float,
        regime: str,
        volume_data:   Optional[Dict],
        direction_data: Optional[Dict],
    ):
        """
        Detecta os 3 setups neurais raros definidos na spec:
          1. Accumulation + Whale Buying
          2. Distribution + Absorption
          3. Compression + Volatility Expansion

        Returns: (is_rare, rare_type, strength)
        """
        # ── Setup 1: Accumulation + Whale Buying ──────────────────────────────
        # Regime ACCUMULATION + bull_prob ≥ 65% + CVD positivo + dir UP
        cvd_up  = bool(volume_data and volume_data.get("cvd_trending_up"))
        dir_up  = direction_data and direction_data.get("direction") == "UP" if direction_data else False
        abs_buy = direction_data and direction_data.get("absorption_type") == "BUY_ABSORPTION" if direction_data else False

        if regime == "ACCUMULATION" and bull >= 65.0 and (cvd_up or abs_buy):
            strength = min(25 + (bull - 65) * 1.5 + (30 if abs_buy else 0) + (20 if cvd_up else 0), 100)
            return True, "ACCUMULATION_WHALE_BUYING", round(strength, 1)

        # ── Setup 2: Distribution + Absorption ────────────────────────────────
        # Regime DISTRIBUTION + bear_prob ≥ 65% + absorção de venda
        abs_sell = direction_data and direction_data.get("absorption_type") == "SELL_ABSORPTION" if direction_data else False
        high_vol = bool(volume_data and volume_data.get("high_volume"))

        if regime == "DISTRIBUTION" and bear >= 65.0 and (abs_sell or (high_vol and not cvd_up)):
            strength = min(25 + (bear - 65) * 1.5 + (30 if abs_sell else 0) + (15 if high_vol else 0), 100)
            return True, "DISTRIBUTION_ABSORPTION", round(strength, 1)

        # ── Setup 3: Compression + Volatility Expansion ────────────────────────
        # Sideways recente ≥ 60% E vexp ≥ 30% E regime VOLATILITY_EXPANSION
        if (side >= 60.0 and vexp >= 30.0) or (regime == REGIME_VOLATILITY_EXPANSION and vexp >= 40.0):
            strength = min(20 + side * 0.3 + vexp * 0.7, 100)
            return True, "COMPRESSION_VOL_EXPANSION", round(strength, 1)

        return False, None, 0.0

    # ── Fallback ────────────────────────────────────────────────────────────────
    def _fallback_output(self, reason: str = "unknown") -> Dict:
        """Output de fallback seguro para erros críticos."""
        return {
            "bull_probability":                 20.0,
            "bear_probability":                 20.0,
            "sideways_probability":             35.0,
            "volatility_expansion_probability": 15.0,
            "manipulation_probability":         10.0,
            "confidence_score":                 20.0,
            "market_regime":                    "RANGING",
            "neural_score":                     50.0,
            "neural_bias":                      "NEUTRAL",
            "bias_strength":                    0.0,
            "bias_confidence":                  20.0,
            "bull_edge":                        0.0,
            "is_balanced":                      True,
            "calibration_note":                 f"fallback: {reason}",
            "rare_neural_setup":                False,
            "rare_neural_type":                 None,
            "rare_neural_strength":             0.0,
            "agreement_score":                  50.0,
            "dominant_model":                   "none",
            "model_confidences":                {},
            "regime_sub_scores":                {},
            "retrain_accuracy":                 0.0,
            "elapsed_ms":                       0.0,
            "timestamp":                        datetime.now().isoformat(),
            "symbol":                           "UNKNOWN",
            "error":                            reason,
        }


# ─── Singleton + Entry point ───────────────────────────────────────────────────
_engine_instance: Optional[NeuralEngine] = None
_engine_lock = __import__("threading").Lock()


def _get_engine() -> NeuralEngine:
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = NeuralEngine()
    return _engine_instance


def run_neural_analysis(
    symbol:         str,
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
) -> Dict:
    """
    Entry point principal do Neural Intelligence Engine v2.
    Usa singleton com cache de 300 segundos.

    Args:
        symbol:         Par de trading (ex: "BTCUSDT")
        df:             DataFrame OHLCV (mín. 64 barras)
        price:          Preço atual
        inst_data:      Output do InstitutionalScoring
        smc_data:       Output do SmartMoneyEngine
        mm_data:        Output do MarketMakerEngine
        pressure_data:  Output do PressureMeter
        rare_data:      Output do RareSetupDetector
        geo_data:       Output do GeopoliticalEngine
        cycle_data:     Output do BitcoinCycleEngine
        direction_data: Output do InstitutionalDirectionEngine
        regime_data:    Output do Regime analyzer
        volume_data:    Output do Volume analyzer
        trend_data:     Output do Trend analyzer
        deriv_data:     Output do Derivatives analyzer

    Returns:
        Dict com análise neural completa
    """
    # Verifica cache
    cache_key = f"neural_{symbol}"
    now = time.time()
    if cache_key in _cache:
        cached_at, cached_data = _cache[cache_key]
        if now - cached_at < CACHE_TTL:
            log.debug(f"[NeuralEngine] Cache hit: {symbol} ({now - cached_at:.0f}s)")
            return cached_data

    # Executa análise
    engine = _get_engine()
    result = engine.analyze(
        df=df,
        price=price,
        symbol=symbol,
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
        smc_mtf_data=smc_mtf_data,
    )

    _cache[cache_key] = (now, result)
    return result
