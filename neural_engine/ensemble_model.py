"""
ensemble_model.py — Weighted Ensemble Combiner
Cap. 17 | Trinity Neural Intelligence Engine v2

Combina as saídas dos 5 modelos individuais em uma distribuição final
de probabilidades usando média ponderada com pesos fixos.

Pesos da ensemble:
  LSTM        × 0.25  → padrões temporais
  Transformer × 0.25  → inteligência multi-timeframe
  CNN         × 0.20  → padrões gráficos
  MLP         × 0.15  → fusão Trinity score
  Regime      × 0.15  → detecção de regime

Recursos:
  - Calibração de confiança baseada na concordância entre modelos
  - Detecção de divergência extrema (sinal de incerteza)
  - Temperatura de softmax adaptativa
  - Metadados de transparência (qual modelo mais influente)
"""

import logging
import numpy as np
from typing import Dict, Optional, List

log = logging.getLogger(__name__)

# ─── Pesos da ensemble ────────────────────────────────────────────────────────
ENSEMBLE_WEIGHTS = {
    "lstm":        0.25,
    "transformer": 0.25,
    "cnn":         0.20,
    "mlp":         0.15,
    "regime":      0.15,
}

KEYS = ["bull_probability", "bear_probability", "sideways_probability",
        "volatility_expansion_probability", "manipulation_probability"]

# Divergência máxima tolerada (pontos percentuais) antes de reduzir confiança
DIVERGENCE_THRESHOLD = 25.0


# ─── Funções auxiliares ────────────────────────────────────────────────────────
def _safe_prob(d: Dict, key: str, default: float = 20.0) -> float:
    try:
        v = float(d.get(key, default))
        return v if (0 <= v <= 100) else default
    except Exception:
        return default


def _softmax_temperature(probs: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    """Aplica temperatura ao softmax para calibrar distribuição."""
    p = np.array(probs, dtype=np.float64)
    p = np.clip(p, 1e-8, None)
    log_p = np.log(p) / temperature
    log_p -= log_p.max()
    exp_p = np.exp(log_p)
    return exp_p / exp_p.sum()


# ─── Ensemble Combiner ────────────────────────────────────────────────────────
class EnsembleModel:
    """
    Combina as saídas dos 5 modelos em uma distribuição final calibrada.
    Calcula métricas de concordância e transparência de decisão.
    """

    def combine(
        self,
        lstm_output:        Dict[str, float],
        transformer_output: Dict[str, float],
        cnn_output:         Dict[str, float],
        mlp_output:         Dict[str, float],
        regime_output:      Dict[str, float],
    ) -> Dict:
        """
        Combina 5 modelos via média ponderada com calibração adaptativa.

        Returns:
            Dict com probabilidades finais, confiança, concordância e metadados.
        """
        try:
            outputs = {
                "lstm":        lstm_output,
                "transformer": transformer_output,
                "cnn":         cnn_output,
                "mlp":         mlp_output,
                "regime":      regime_output,
            }

            # ── 1. Média ponderada das probabilidades ────────────────────────
            combined = {k: 0.0 for k in KEYS}
            for model_name, model_out in outputs.items():
                w = ENSEMBLE_WEIGHTS[model_name]
                for key in KEYS:
                    combined[key] += _safe_prob(model_out, key) * w

            # ── 2. Normalização para soma = 100 ───────────────────────────────
            total = sum(combined.values()) + 1e-8
            for k in KEYS:
                combined[k] = round(combined[k] / total * 100.0, 1)

            # ── 3. Confiança média ponderada dos modelos ───────────────────────
            raw_confidences = {
                m: _safe_prob(outputs[m], "confidence_score", 50.0)
                for m in outputs
            }
            weighted_confidence = sum(
                raw_confidences[m] * ENSEMBLE_WEIGHTS[m] for m in outputs
            )

            # ── 4. Concordância entre modelos ────────────────────────────────
            agreement_score, divergence_info = self._compute_agreement(outputs)

            # ── 5. Ajuste de confiança por concordância ────────────────────────
            # Alta concordância → amplifica confiança; baixa → reduz
            agreement_factor = 0.7 + agreement_score * 0.5  # [0.7, 1.2]
            final_confidence = min(weighted_confidence * agreement_factor, 97.0)

            # ── 6. Modelo mais influente ───────────────────────────────────────
            dominant_key = max(KEYS, key=lambda k: combined[k])
            dominant_model = self._find_dominant_model(outputs, dominant_key)

            # ── 7. Temperatura adaptativa ─────────────────────────────────────
            # Se concordância baixa → temperatura alta (distribui mais uniformemente)
            temperature = 1.0 if agreement_score > 0.7 else (1.0 + (0.7 - agreement_score) * 1.5)
            probs_raw = np.array([combined[k] for k in KEYS], dtype=np.float64)
            probs_cal = _softmax_temperature(probs_raw, temperature) * 100.0
            for i, k in enumerate(KEYS):
                combined[k] = round(float(probs_cal[i]), 1)

            # ── 8. Score neural sintético (0-100) ────────────────────────────
            # Representa a qualidade do sinal neural
            bull = combined["bull_probability"]
            bear = combined["bear_probability"]
            edge = abs(bull - bear)
            neural_score = round(
                50.0 + edge * 0.4 + final_confidence * 0.15 + agreement_score * 15.0,
                1
            )
            neural_score = min(neural_score, 99.0)

            return {
                **combined,
                "confidence_score":    round(final_confidence, 1),
                "agreement_score":     round(agreement_score * 100, 1),  # 0-100
                "divergence_info":     divergence_info,
                "neural_score":        neural_score,
                "dominant_model":      dominant_model,
                "dominant_key":        dominant_key,
                "model_confidences":   {m: round(raw_confidences[m], 1) for m in outputs},
                "temperature_applied": round(temperature, 2),
            }

        except Exception as e:
            log.error(f"[EnsembleModel] Erro: {e}", exc_info=True)
            return self._neutral_output()

    def _compute_agreement(self, outputs: Dict) -> tuple:
        """
        Calcula a concordância entre os modelos.
        Baseada no desvio padrão das probabilidades de cada classe entre modelos.
        """
        try:
            divergence_by_class = {}
            model_names = list(outputs.keys())
            max_divergence = 0.0

            for key in KEYS:
                probs_per_model = [_safe_prob(outputs[m], key) for m in model_names]
                std_dev = np.std(probs_per_model)
                divergence_by_class[key] = round(std_dev, 1)
                max_divergence = max(max_divergence, std_dev)

            # Concordância = inverso da divergência média
            avg_divergence = np.mean(list(divergence_by_class.values()))

            # Normaliza: 0 divergência = 1.0, DIVERGENCE_THRESHOLD = 0.0
            agreement = max(0.0, 1.0 - avg_divergence / DIVERGENCE_THRESHOLD)

            # Classe mais divergente
            most_divergent = max(divergence_by_class, key=divergence_by_class.get)

            divergence_info = {
                "by_class":      divergence_by_class,
                "avg_divergence": round(avg_divergence, 1),
                "max_divergence": round(max_divergence, 1),
                "most_divergent_class": most_divergent,
                "high_disagreement": avg_divergence > DIVERGENCE_THRESHOLD * 0.6,
            }

            return agreement, divergence_info

        except Exception as e:
            log.warning(f"[EnsembleModel] Erro no cálculo de concordância: {e}")
            return 0.5, {}

    def _find_dominant_model(self, outputs: Dict, dominant_key: str) -> str:
        """Identifica qual modelo mais contribuiu para o output dominante."""
        try:
            # Modelo com maior probabilidade na classe dominante, ponderado pelo peso
            contributions = {
                m: _safe_prob(outputs[m], dominant_key) * ENSEMBLE_WEIGHTS[m]
                for m in outputs
            }
            return max(contributions, key=contributions.get)
        except Exception:
            return "unknown"

    def _neutral_output(self) -> Dict:
        """Output neutro de fallback."""
        return {
            "bull_probability":                 20.0,
            "bear_probability":                 20.0,
            "sideways_probability":             35.0,
            "volatility_expansion_probability": 15.0,
            "manipulation_probability":         10.0,
            "confidence_score":                 25.0,
            "agreement_score":                  50.0,
            "divergence_info":                  {},
            "neural_score":                     50.0,
            "dominant_model":                   "none",
            "dominant_key":                     "sideways_probability",
            "model_confidences":                {},
            "temperature_applied":              1.5,
        }
