"""
bias_balancer.py — Motor de Viés Market-Neutral
Cap. 17 | Trinity Neural Intelligence Engine v2

Garante que o sistema permaneça market-neutral e não desenvolva viés bull/bear
sistemático. Analisa as probabilidades do ensemble e determina o viés de forma
balanceada e adaptativa.

Princípios:
  - LONG   se bull_probability > bear_probability + THRESHOLD_LONG
  - SHORT  se bear_probability > bull_probability + THRESHOLD_SHORT
  - NEUTRAL se spread dentro da zona de incerteza

Proteções anti-viés:
  - Calibração dinâmica baseada no histórico recente
  - Penalty se bull/bear dominante por muitos ciclos consecutivos
  - Detecção de deriva sistemática de modelo

Output:
  {
    "neural_bias":      LONG | SHORT | NEUTRAL,
    "bias_strength":    0-100,
    "bias_confidence":  0-100,
    "bull_edge":        diferença bull-bear,
    "is_balanced":      bool,
    "calibration_note": str,
  }
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from collections import deque

log = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────
THRESHOLD_LONG  = 8.0   # bull precisa superar bear por ≥ 8 pontos para LONG
THRESHOLD_SHORT = 8.0   # bear precisa superar bull por ≥ 8 pontos para SHORT
MAX_HISTORY     = 50    # ciclos de histórico para calibração
BIAS_DRIFT_WARN = 15    # avisar se mesmo viés por mais de N ciclos consecutivos
CALIBRATION_WINDOW = 20 # janela para detectar deriva de modelo


# ─── Output ───────────────────────────────────────────────────────────────────
@dataclass
class BiasResult:
    neural_bias:      str    # LONG | SHORT | NEUTRAL
    bias_strength:    float  # 0-100
    bias_confidence:  float  # 0-100
    bull_edge:        float  # bull_prob - bear_prob
    is_balanced:      bool   # sistema em equilíbrio
    calibration_note: str    # nota de calibração


# ─── Bias Balancer ────────────────────────────────────────────────────────────
class BiasBalancer:
    """
    Motor de viés market-neutral com calibração adaptativa.

    Opera sobre as probabilidades consolidadas do ensemble para produzir
    a direção de viés final, resistindo a desvios sistemáticos.
    """

    def __init__(self):
        self._bias_history: deque = deque(maxlen=MAX_HISTORY)
        self._bull_history: deque = deque(maxlen=MAX_HISTORY)
        self._bear_history: deque = deque(maxlen=MAX_HISTORY)
        self._consecutive_same: int = 0
        self._last_bias: str = "NEUTRAL"

    def compute(
        self,
        bull_prob:   float,
        bear_prob:   float,
        side_prob:   float,
        vexp_prob:   float,
        manip_prob:  float,
        confidence:  float,
        regime:      str,
    ) -> BiasResult:
        """
        Computa o viés market-neutral final.

        Args:
            bull_prob:   probabilidade bullish (0-100)
            bear_prob:   probabilidade bearish (0-100)
            side_prob:   probabilidade sideways (0-100)
            vexp_prob:   probabilidade volatilidade expansão (0-100)
            manip_prob:  probabilidade manipulação (0-100)
            confidence:  confiança global do ensemble (0-100)
            regime:      regime de mercado detectado

        Returns:
            BiasResult com viés, força, confiança e metadados de calibração
        """
        try:
            # ── Calibração dinâmica ────────────────────────────────────────────
            # Ajusta thresholds com base no histórico recente
            threshold_l, threshold_s = self._adaptive_thresholds()

            # ── Calcula edge bull vs bear ──────────────────────────────────────
            bull_edge = bull_prob - bear_prob

            # ── Aplica penalidade de deriva sistemática ───────────────────────
            drift_penalty = self._compute_drift_penalty()
            bull_adj = bull_prob - drift_penalty["bull_adj"]
            bear_adj = bear_prob - drift_penalty["bear_adj"]
            edge_adj = bull_adj - bear_adj

            # ── Lógica de viés principal ───────────────────────────────────────
            bias_from_regime = self._regime_bias_modifier(regime)

            if edge_adj >= threshold_l and confidence >= 45:
                bias = "LONG"
            elif edge_adj <= -threshold_s and confidence >= 45:
                bias = "SHORT"
            else:
                bias = "NEUTRAL"

            # ── Ajuste por regime ──────────────────────────────────────────────
            if bias_from_regime == "FORCE_NEUTRAL":
                # Manipulação detectada → forçar neutro
                bias = "NEUTRAL"
            elif bias_from_regime == "REDUCE_CONFIDENCE" and confidence < 60:
                # Regime incerto com baixa confiança → neutro
                bias = "NEUTRAL"

            # ── Força do viés (0-100) ─────────────────────────────────────────
            if bias == "LONG":
                bias_strength = min(abs(edge_adj) * 2.5 + confidence * 0.3, 100.0)
            elif bias == "SHORT":
                bias_strength = min(abs(edge_adj) * 2.5 + confidence * 0.3, 100.0)
            else:
                # Neutral: força baseada na probabilidade sideways
                bias_strength = min(side_prob * 0.7 + (100 - abs(bull_edge)) * 0.3, 100.0)

            # ── Confiança do viés ─────────────────────────────────────────────
            bias_confidence = min(confidence * 0.7 + abs(edge_adj) * 1.2, 99.0)

            # ── Equilíbrio (True se sistema não está em deriva) ───────────────
            is_balanced = self._is_balanced()

            # ── Nota de calibração ────────────────────────────────────────────
            note = self._calibration_note(bull_edge, drift_penalty, is_balanced)

            # ── Atualiza histórico ─────────────────────────────────────────────
            self._update_history(bias, bull_prob, bear_prob)

            return BiasResult(
                neural_bias=bias,
                bias_strength=round(bias_strength, 1),
                bias_confidence=round(bias_confidence, 1),
                bull_edge=round(bull_edge, 2),
                is_balanced=is_balanced,
                calibration_note=note,
            )

        except Exception as e:
            log.error(f"[BiasBalancer] Erro: {e}", exc_info=True)
            return BiasResult(
                neural_bias="NEUTRAL",
                bias_strength=0.0,
                bias_confidence=30.0,
                bull_edge=0.0,
                is_balanced=True,
                calibration_note=f"erro: {e}",
            )

    # ── Calibração adaptativa ──────────────────────────────────────────────────
    def _adaptive_thresholds(self):
        """
        Ajusta thresholds dinamicamente.
        Em ambientes de alta volatilidade histórica, aumenta o threshold
        para evitar flip de viés.
        """
        if len(self._bull_history) < 10:
            return THRESHOLD_LONG, THRESHOLD_SHORT

        bull_std = np.std(list(self._bull_history)[-10:])
        bear_std = np.std(list(self._bear_history)[-10:])
        vol_adj  = (bull_std + bear_std) * 0.2

        # Range: [6, 14] para evitar extremos
        threshold_l = max(6.0, min(THRESHOLD_LONG + vol_adj, 14.0))
        threshold_s = max(6.0, min(THRESHOLD_SHORT + vol_adj, 14.0))
        return threshold_l, threshold_s

    def _compute_drift_penalty(self) -> Dict[str, float]:
        """
        Detecta e penaliza deriva sistemática de modelo.
        Se bull sempre > 60 nos últimos N ciclos → aplica penalty no bull.
        """
        if len(self._bull_history) < CALIBRATION_WINDOW:
            return {"bull_adj": 0.0, "bear_adj": 0.0, "has_drift": False}

        recent_bull = list(self._bull_history)[-CALIBRATION_WINDOW:]
        recent_bear = list(self._bear_history)[-CALIBRATION_WINDOW:]

        avg_bull = np.mean(recent_bull)
        avg_bear = np.mean(recent_bear)

        bull_drift = max(0, avg_bull - 50.0)
        bear_drift = max(0, avg_bear - 50.0)

        # Penalidade proporcional à deriva (0-8 pontos)
        bull_adj = min(bull_drift * 0.25, 8.0)
        bear_adj = min(bear_drift * 0.25, 8.0)
        has_drift = (bull_adj > 3.0) or (bear_adj > 3.0)

        return {"bull_adj": bull_adj, "bear_adj": bear_adj, "has_drift": has_drift}

    def _regime_bias_modifier(self, regime: str) -> str:
        """Ajusta o tratamento do viés com base no regime detectado."""
        if regime == "MANIPULATION":
            return "FORCE_NEUTRAL"
        if regime == "VOLATILITY_EXPANSION":
            return "REDUCE_CONFIDENCE"
        if regime == "REVERSAL":
            return "REDUCE_CONFIDENCE"
        return "NORMAL"

    def _is_balanced(self) -> bool:
        """
        Verifica se o sistema está equilibrado.
        Não equilibrado se: mesmo viés por muitos ciclos consecutivos.
        """
        if len(self._bias_history) < 5:
            return True
        recent = list(self._bias_history)[-BIAS_DRIFT_WARN:]
        if len(recent) < BIAS_DRIFT_WARN:
            return True
        # Se todos os últimos N viéses são iguais (e não NEUTRAL) → desequilíbrio
        if len(set(recent)) == 1 and recent[0] != "NEUTRAL":
            return False
        # Distribuição muito assimétrica
        bull_frac  = recent.count("LONG") / len(recent)
        bear_frac  = recent.count("SHORT") / len(recent)
        if bull_frac > 0.85 or bear_frac > 0.85:
            return False
        return True

    def _calibration_note(self, bull_edge: float, drift: Dict, balanced: bool) -> str:
        """Gera nota de calibração para logging e dashboard."""
        parts = []
        if drift.get("has_drift"):
            if drift["bull_adj"] > drift["bear_adj"]:
                parts.append(f"drift_bull:{drift['bull_adj']:.1f}pts")
            else:
                parts.append(f"drift_bear:{drift['bear_adj']:.1f}pts")
        if not balanced:
            parts.append("!desequilibrio_detectado")
        if abs(bull_edge) < THRESHOLD_LONG * 0.5:
            parts.append("zona_neutra")
        return " | ".join(parts) if parts else "calibrado"

    def _update_history(self, bias: str, bull_prob: float, bear_prob: float):
        """Atualiza histórico para calibração futura."""
        self._bias_history.append(bias)
        self._bull_history.append(bull_prob)
        self._bear_history.append(bear_prob)

        if bias == self._last_bias and bias != "NEUTRAL":
            self._consecutive_same += 1
            if self._consecutive_same >= BIAS_DRIFT_WARN:
                log.warning(
                    f"[BiasBalancer] Viés '{bias}' consecutivo há {self._consecutive_same} ciclos "
                    f"— possível deriva de modelo."
                )
        else:
            self._consecutive_same = 0
        self._last_bias = bias

    def get_calibration_stats(self) -> Dict:
        """Retorna estatísticas de calibração para monitoramento."""
        if not self._bias_history:
            return {}
        history = list(self._bias_history)
        n = len(history)
        return {
            "total_signals":     n,
            "long_pct":          round(history.count("LONG")  / n * 100, 1),
            "short_pct":         round(history.count("SHORT") / n * 100, 1),
            "neutral_pct":       round(history.count("NEUTRAL") / n * 100, 1),
            "consecutive_same":  self._consecutive_same,
            "is_balanced":       self._is_balanced(),
            "avg_bull_prob":     round(np.mean(list(self._bull_history)), 1) if self._bull_history else 50.0,
            "avg_bear_prob":     round(np.mean(list(self._bear_history)), 1) if self._bear_history else 50.0,
        }
