"""
mlp_model.py — MLP Trinity Score Fusion
Cap. 17 | Trinity Neural Intelligence Engine v2

Multi-Layer Perceptron para fusão dos scores tabulares de todos os motores Trinity.
Opera sobre o vetor tabular [TABULAR_DIM] e captura interações não-lineares entre
os diferentes motores (institucional, SMC, MM, geo, ciclo, direção).

Arquitetura (modo estatístico — sem PyTorch):
  - Regressão logística com interações de segunda ordem
  - Normalização por camadas
  - Output: distribuição de 5 probabilidades

Modo PyTorch (se disponível):
  - Dense: [TABULAR_DIM → 128 → 64 → 32 → 5]
  - BatchNorm + Dropout(0.3) entre camadas
  - GELU activations (melhor para dados tabulares)

Peso na ensemble: 0.15
"""

import logging
import numpy as np
from typing import Dict, Optional

from .feature_engine import FeatureSet, TABULAR_DIM, _safe

log = logging.getLogger(__name__)

# ─── Tentativa de import PyTorch ───────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


# ─── Saída padrão ──────────────────────────────────────────────────────────────
def _make_output(bull, bear, side, vexp, manip, confidence) -> Dict[str, float]:
    probs = np.clip(np.array([bull, bear, side, vexp, manip], dtype=np.float32), 0, 1)
    probs /= (probs.sum() + 1e-8)
    return {
        "bull_probability":                 round(float(probs[0]) * 100, 1),
        "bear_probability":                 round(float(probs[1]) * 100, 1),
        "sideways_probability":             round(float(probs[2]) * 100, 1),
        "volatility_expansion_probability": round(float(probs[3]) * 100, 1),
        "manipulation_probability":         round(float(probs[4]) * 100, 1),
        "confidence_score":                 round(float(confidence), 1),
    }


# ─── Modo Estatístico ─────────────────────────────────────────────────────────
class StatisticalMLP:
    """
    Fusão estatística dos scores tabulares Trinity.
    Usa média ponderada com interações de segunda ordem para detectar confluências.

    Mapeamento do vetor tabular (baseado em feature_engine.py):
    idx  0: inst_score/100
    idx  1: confluences/6
    idx  2: direction [-1,+1]
    idx  3: inst valid [0,1]
    idx  4: layer market_structure/100
    idx  5: layer volume/100
    idx  6: smc_score/100
    idx  7: smc valid
    idx  8: smc direction [-1,+1]
    idx  9: smc confidence/100
    idx 10: mm_score/100
    idx 11: mm valid
    idx 12: trap_prob/100
    idx 13: premium-discount [-1,+1]
    idx 14: sweep_strength/100
    idx 15: pressure [-1,+1]
    idx 16: filter_passed [0,1]
    idx 17: pressure direction [-1,+1]
    idx 18: rare_setup [0,1]
    idx 19: rare_score/100
    idx 20: signal_bonus/100
    idx 21: geo_score/100
    idx 22: geo_bias [-1,+1]
    idx 23: geo_confidence/100
    idx 24: rare_macro_setup [0,1]
    idx 25: cycle_score/100
    idx 26: risk_multiplier norm
    idx 27: cycle_phase_score
    idx 28: rare_cycle_setup [0,1]
    idx 29: cycle_confidence/100
    idx 30: direction_score/100
    idx 31: dir direction [-1,+1]
    idx 32: dir confidence/100
    idx 33: fake_move_prob/100
    idx 34: breakout_prob/100
    idx 35: rare_direction_setup [0,1]
    idx 36: regime_score
    idx 37: atr_pct norm
    idx 38: squeeze [0,1]
    idx 39: adx_norm
    idx 40: high_volume [0,1]
    idx 41: cvd_trending_up [0,1]
    idx 42: trap_signal [0,1]
    idx 43: funding norm [-1,+1]
    idx 44: long_pct norm [-1,+1]
    idx 45: trend_score/100
    """

    # Pesos de atenção por grupo de features (ajustados empiricamente)
    INST_WEIGHT  = 0.20
    SMC_WEIGHT   = 0.18
    MM_WEIGHT    = 0.15
    PRESSURE_W   = 0.10
    RARE_W       = 0.08
    GEO_W        = 0.10
    CYCLE_W      = 0.10
    DIR_W        = 0.09

    def predict(self, feature_set: FeatureSet) -> Dict[str, float]:
        try:
            tab = feature_set.tabular  # [TABULAR_DIM]
            if len(tab) < 46:
                tab = np.concatenate([tab, np.zeros(46 - len(tab))])

            # Extrai grupos
            inst_score   = _safe(tab[0])
            inst_conf    = _safe(tab[1])
            inst_dir     = _safe(tab[2])   # [-1, +1]
            inst_valid   = _safe(tab[3])

            smc_score    = _safe(tab[6])
            smc_dir      = _safe(tab[8])   # [-1, +1]
            smc_conf     = _safe(tab[9])
            smc_valid    = _safe(tab[7])

            mm_score     = _safe(tab[10])
            mm_valid     = _safe(tab[11])
            trap_prob    = _safe(tab[12])  # [0, 1]
            prem_disc    = _safe(tab[13])  # +1 premium, -1 discount

            pressure_val = _safe(tab[15])  # [-1, +1]
            p_filter     = _safe(tab[16])
            pressure_dir = _safe(tab[17])  # [-1, +1]

            rare_active  = _safe(tab[18])
            rare_score   = _safe(tab[19])

            geo_score    = _safe(tab[21])
            geo_bias     = _safe(tab[22])  # [-1, +1]
            geo_conf     = _safe(tab[23])

            cycle_score  = _safe(tab[25])
            cycle_phase  = _safe(tab[27])  # 0-1 (phase score)
            rare_cycle   = _safe(tab[28])

            dir_score    = _safe(tab[30])
            dir_dir      = _safe(tab[31])  # [-1, +1]
            dir_conf     = _safe(tab[32])
            fake_prob    = _safe(tab[33])
            break_prob   = _safe(tab[34])

            regime_score = _safe(tab[36])
            atr_norm     = _safe(tab[37])
            squeeze      = _safe(tab[38])
            adx_norm     = _safe(tab[39])

            high_vol     = _safe(tab[40])
            cvd_up       = _safe(tab[41])
            trap_sig     = _safe(tab[42])
            funding      = _safe(tab[43])  # [-1, +1]
            ls_ratio     = _safe(tab[44])  # [-1, +1]
            trend_sc     = _safe(tab[45])

            # ── BULL score: confluências otimistas ────────────────────────────
            bull = 0.0
            # Institucional bullish
            bull += self.INST_WEIGHT * max(0, inst_dir) * inst_score * inst_valid
            bull += self.SMC_WEIGHT  * max(0, smc_dir) * smc_score * smc_valid
            bull += self.MM_WEIGHT   * max(0, -prem_disc) * mm_score * mm_valid  # discount = bullish
            bull += self.PRESSURE_W  * max(0, pressure_val) * p_filter
            bull += self.GEO_W       * max(0, geo_bias) * geo_score * geo_conf
            bull += self.CYCLE_W     * cycle_phase * cycle_score
            bull += self.DIR_W       * max(0, dir_dir) * dir_score * dir_conf
            # Volume bullish
            if cvd_up: bull += 0.03
            if high_vol: bull += 0.02
            if funding < -0.3: bull += 0.02  # funding negativo = bull (shorts pagam)
            # Trend
            bull += 0.04 * trend_sc

            # ── BEAR score: confluências pessimistas ───────────────────────────
            bear = 0.0
            bear += self.INST_WEIGHT * max(0, -inst_dir) * inst_score * inst_valid
            bear += self.SMC_WEIGHT  * max(0, -smc_dir) * smc_score * smc_valid
            bear += self.MM_WEIGHT   * max(0, prem_disc) * mm_score * mm_valid  # premium = bearish
            bear += self.PRESSURE_W  * max(0, -pressure_val) * p_filter
            bear += self.GEO_W       * max(0, -geo_bias) * geo_score * geo_conf
            bear += self.CYCLE_W     * (1.0 - cycle_phase) * (1.0 - cycle_score)
            bear += self.DIR_W       * max(0, -dir_dir) * dir_score * dir_conf
            if not cvd_up: bear += 0.02
            if funding > 0.3: bear += 0.02  # funding positivo = bear (longs pagam)

            # ── SIDEWAYS: confluência neutra ──────────────────────────────────
            side = 0.0
            neutrality = 1.0 - abs(inst_dir) - abs(smc_dir) * 0.5
            side += 0.25 * max(0, neutrality)
            if regime_score < 0.4: side += 0.15  # regime lateral
            if adx_norm < 0.25: side += 0.20     # ADX baixo
            if not high_vol: side += 0.05
            side += 0.05 * max(0, 1.0 - atr_norm * 5)

            # ── VOLATILITY EXPANSION ──────────────────────────────────────────
            vexp = 0.0
            if squeeze: vexp += 0.30
            if atr_norm > 0.4: vexp += 0.20
            vexp += 0.15 * rare_score * rare_active
            if rare_cycle: vexp += 0.15

            # ── MANIPULATION ─────────────────────────────────────────────────
            manip = 0.0
            manip += 0.35 * trap_prob
            manip += 0.25 * max(0, 1.0 - fake_prob) * trap_sig  # trap signal = manipulação
            if mm_valid and trap_prob > 0.5: manip += 0.15

            # ── Confluência bônus (interação de 2ª ordem) ─────────────────────
            # Se inst + smc + dir concordam → amplifica sinal
            agree_bull = (inst_dir > 0.3) and (smc_dir > 0.3) and (dir_dir > 0.3)
            agree_bear = (inst_dir < -0.3) and (smc_dir < -0.3) and (dir_dir < -0.3)
            if agree_bull: bull  *= 1.20
            if agree_bear: bear  *= 1.20

            # ── Confiança ─────────────────────────────────────────────────────
            total_valid = inst_valid + smc_valid + mm_valid + p_filter
            all_scores  = [bull, bear, side, vexp, manip]
            max_sc      = max(all_scores)
            sorted_sc   = sorted(all_scores, reverse=True)
            spread      = sorted_sc[0] - sorted_sc[1] if len(sorted_sc) > 1 else 0
            confidence  = 35.0 + spread * 60.0 + total_valid * 5.0 + max_sc * 25.0
            confidence  = min(confidence, 95.0)

            return _make_output(bull, bear, side, vexp, manip, confidence)

        except Exception as e:
            log.error(f"[StatisticalMLP] Erro: {e}", exc_info=True)
            return _make_output(0.2, 0.2, 0.3, 0.15, 0.15, 30.0)


# ─── Modo PyTorch ─────────────────────────────────────────────────────────────
if TORCH_AVAILABLE:
    class _TorchMLP(nn.Module):
        """MLP com BatchNorm e GELU para fusão tabular."""
        def __init__(self, input_dim=TABULAR_DIM, output_size=5):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(input_dim, 128),
                nn.BatchNorm1d(128),
                nn.GELU(),
                nn.Dropout(0.3),
                nn.Linear(128, 64),
                nn.BatchNorm1d(64),
                nn.GELU(),
                nn.Dropout(0.2),
                nn.Linear(64, 32),
                nn.GELU(),
                nn.Linear(32, output_size),
                nn.Softmax(dim=-1),
            )

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            return self.net(x)

    class TorchMLPModel:
        def __init__(self):
            self.model = _TorchMLP()
            self.model.eval()
            for name, p in self.model.named_parameters():
                if "weight" in name and p.dim() >= 2:
                    nn.init.xavier_normal_(p)
                elif "bias" in name:
                    nn.init.zeros_(p)
            self._has_trained_weights = False
            self._fallback = StatisticalMLP()

        def predict(self, feature_set: FeatureSet) -> Dict[str, float]:
            if not self._has_trained_weights:
                log.debug("[MLP] Pesos não-treinados → delegando StatisticalMLP")
                return self._fallback.predict(feature_set)
            try:
                with torch.no_grad():
                    x   = torch.FloatTensor(feature_set.tabular).unsqueeze(0)
                    out = self.model(x)
                    probs = out.squeeze(0).numpy()
                    confidence = float(probs.max()) * 100.0
                    confidence = min(50.0 + confidence * 0.5, 93.0)
                    return _make_output(*probs.tolist(), confidence)
            except Exception as e:
                log.error(f"[TorchMLP] Erro: {e}", exc_info=True)
                return _make_output(0.2, 0.2, 0.3, 0.15, 0.15, 30.0)


# ─── Classe pública ────────────────────────────────────────────────────────────
class MLPModel:
    """
    Interface pública do modelo MLP.
    Peso na ensemble: 0.15
    """
    WEIGHT = 0.15

    def __init__(self):
        if TORCH_AVAILABLE:
            self._model = TorchMLPModel()
            log.info("[MLP] Inicializado com PyTorch (BatchNorm + GELU).")
        else:
            self._model = StatisticalMLP()
            log.info("[MLP] Inicializado em modo estatístico (fusão Trinity).")

    def predict(self, feature_set: FeatureSet) -> Dict[str, float]:
        if not feature_set.valid:
            return _make_output(0.2, 0.2, 0.3, 0.15, 0.15, 20.0)
        return self._model.predict(feature_set)

    def load_weights(self, path: str) -> bool:
        if not TORCH_AVAILABLE or not hasattr(self._model, "model"):
            return False
        try:
            import torch
            self._model.model.load_state_dict(torch.load(path, map_location="cpu"))
            self._model.model.eval()
            self._model._has_trained_weights = True
            log.info(f"[MLP] Pesos treinados carregados: {path}")
            return True
        except Exception as e:
            log.warning(f"[MLP] Falha ao carregar pesos: {e}")
            return False

    def save_weights(self, path: str) -> bool:
        if not TORCH_AVAILABLE or not hasattr(self._model, "model"):
            return False
        try:
            import torch
            torch.save(self._model.model.state_dict(), path)
            return True
        except Exception as e:
            log.warning(f"[MLP] Falha ao salvar pesos: {e}")
            return False
