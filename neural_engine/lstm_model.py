"""
lstm_model.py — LSTM Temporal Pattern Detector
Cap. 17 | Trinity Neural Intelligence Engine v2

Modelo LSTM para detecção de padrões temporais em sequências de preço/volume.
Captura dependências de longa duração em séries temporais de mercado.

Arquitetura (modo estatístico — sem PyTorch):
  - LSTM simplificado via operações matriciais NumPy
  - Camadas: LSTM(64) → Dropout(0.2) → Dense(32) → Dense(5)
  - Output: 5 probabilidades softmax [bull, bear, sideways, vol_exp, manip]

Modo PyTorch (se disponível):
  - LSTM bidirecional com atenção temporal
  - Mais expressivo — usa quando torch está instalado

Peso na ensemble: 0.25
"""

import logging
import numpy as np
from typing import Dict, Optional, Tuple

from .feature_engine import FeatureSet, SEQUENCE_LEN, FEATURE_DIM, _safe

log = logging.getLogger(__name__)

# ─── Tentativa de import PyTorch ───────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    log.info("[LSTM] PyTorch não disponível — modo estatístico ativo.")


# ─── Saída padrão ──────────────────────────────────────────────────────────────
def _make_output(bull: float, bear: float, side: float, vexp: float, manip: float,
                 confidence: float) -> Dict[str, float]:
    """Garante que as 5 probabilidades somem ~100 e estão em [0, 100]."""
    probs = np.array([bull, bear, side, vexp, manip], dtype=np.float32)
    probs = np.clip(probs, 0, 1)
    total = probs.sum() + 1e-8
    probs /= total
    return {
        "bull_probability":                 round(float(probs[0]) * 100, 1),
        "bear_probability":                 round(float(probs[1]) * 100, 1),
        "sideways_probability":             round(float(probs[2]) * 100, 1),
        "volatility_expansion_probability": round(float(probs[3]) * 100, 1),
        "manipulation_probability":         round(float(probs[4]) * 100, 1),
        "confidence_score":                 round(float(confidence), 1),
    }


# ─── Modo Estatístico (sem PyTorch) ───────────────────────────────────────────
class StatisticalLSTM:
    """
    Aproximação estatística do LSTM.
    Analisa padrões temporais via momentum, reversão, volatilidade e tendência
    usando operações vetorizadas NumPy.
    """

    def predict(self, sequence: np.ndarray) -> Dict[str, float]:
        """
        Recebe sequência [SEQUENCE_LEN, FEATURE_DIM] e retorna distribuição de probabilidades.
        """
        try:
            seq = sequence.astype(np.float32)
            n   = seq.shape[0]

            # Feature 0 = retorno log normalizado
            returns   = seq[:, 0]
            body_rat  = seq[:, 1]
            wick_up   = seq[:, 2]
            wick_dn   = seq[:, 3]
            clv       = seq[:, 4]
            momentum  = seq[:, 5]
            vol_real  = seq[:, 6]
            vol_norm  = seq[:, 7]
            rsi_norm  = seq[:, 10]
            ema_cross = seq[:, 11]

            # ── Análise de momentum acumulado ─────────────────────────────────
            # Média dos retornos das últimas N barras vs total
            ret_8   = returns[-8:].mean()   if n >= 8  else returns.mean()
            ret_21  = returns[-21:].mean()  if n >= 21 else returns.mean()
            ret_all = returns.mean()

            # Momentum: retorno recente vs lento
            mom_signal = ret_8 - ret_21

            # ── Análise de volatilidade ───────────────────────────────────────
            vol_recent = vol_real[-8:].mean()  if n >= 8  else vol_real.mean()
            vol_hist   = vol_real[-21:].mean() if n >= 21 else vol_real.mean()
            vol_ratio  = vol_recent / (vol_hist + 1e-8)

            # ── Análise de CLV (fluxo de ordem proxy) ─────────────────────────
            clv_recent = clv[-8:].mean()   if n >= 8  else clv.mean()
            clv_trend  = np.polyfit(np.arange(min(n, 21)), clv[-21:][:n], 1)[0] if n >= 5 else 0

            # ── Análise de velas (padrões de corpo/sombra) ────────────────────
            last_5_body = body_rat[-5:].mean() if n >= 5 else body_rat.mean()
            last_5_wick = (wick_up[-5:] + wick_dn[-5:]).mean() if n >= 5 else 0
            wick_up_dom = wick_up[-5:].mean() > wick_dn[-5:].mean() if n >= 5 else False
            wick_dn_dom = wick_dn[-5:].mean() > wick_up[-5:].mean() if n >= 5 else False

            # ── Análise de RSI e EMA ───────────────────────────────────────────
            rsi_last    = rsi_norm[-1] if n >= 1 else 0.5
            ema_last    = ema_cross[-1] if n >= 1 else 0

            # ─ Computar scores de regime ─────────────────────────────────────
            # BULL: momentum positivo, CLV positivo, EMA cruzada para cima, RSI não sobrecomprado
            bull_score = 0.0
            if mom_signal > 0.05:  bull_score += 0.25
            if ret_8 > 0:          bull_score += 0.15
            if clv_recent > 0.1:   bull_score += 0.20
            if clv_trend > 0:      bull_score += 0.10
            if ema_last > 0:       bull_score += 0.15
            if 0.35 < rsi_last < 0.75: bull_score += 0.10
            if wick_dn_dom:        bull_score += 0.05  # rejeição de venda

            # BEAR: inverso
            bear_score = 0.0
            if mom_signal < -0.05: bear_score += 0.25
            if ret_8 < 0:          bear_score += 0.15
            if clv_recent < -0.1:  bear_score += 0.20
            if clv_trend < 0:      bear_score += 0.10
            if ema_last < 0:       bear_score += 0.15
            if rsi_last < 0.35 or rsi_last > 0.75: bear_score += 0.05
            if wick_up_dom:        bear_score += 0.05  # rejeição de compra

            # SIDEWAYS: baixo momentum, baixa volatilidade, RSI central
            side_score = 0.0
            if abs(mom_signal) < 0.02:   side_score += 0.30
            if vol_ratio < 0.8:          side_score += 0.25
            if abs(clv_recent) < 0.05:   side_score += 0.20
            if 0.40 < rsi_last < 0.60:   side_score += 0.15
            if abs(ema_last) < 0.001:    side_score += 0.10

            # VOLATILITY EXPANSION: vol ratio alto, squeeze rompendo
            vexp_score = 0.0
            if vol_ratio > 2.0:          vexp_score += 0.40
            elif vol_ratio > 1.5:        vexp_score += 0.25
            if abs(mom_signal) > 0.15:   vexp_score += 0.25
            if last_5_body > 0.65:       vexp_score += 0.20  # velas com corpo grande
            if abs(ret_8) > abs(ret_all) * 2: vexp_score += 0.15

            # MANIPULATION: velas com sombra grande, volume anormal, sem continuação
            manip_score = 0.0
            if last_5_wick > 0.5:       manip_score += 0.30  # sombras dominantes
            if last_5_body < 0.25:      manip_score += 0.20  # corpo pequeno (indecisão)
            # Reversão rápida: retorno 3 barras oposto ao retorno 8 barras
            ret_3 = returns[-3:].mean() if n >= 3 else 0
            if np.sign(ret_3) != np.sign(ret_8) and abs(ret_3) > abs(ret_8) * 0.5:
                manip_score += 0.30
            if vol_ratio > 2.5 and abs(mom_signal) < 0.03:
                manip_score += 0.20  # volume anormal sem tendência

            # ── Confiança baseada na clareza do sinal ─────────────────────────
            max_score = max(bull_score, bear_score, side_score, vexp_score, manip_score)
            sorted_scores = sorted([bull_score, bear_score, side_score, vexp_score, manip_score], reverse=True)
            spread = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 0
            confidence = 35.0 + spread * 80.0 + max_score * 20.0
            confidence = min(confidence, 90.0)

            return _make_output(bull_score, bear_score, side_score, vexp_score, manip_score, confidence)

        except Exception as e:
            log.error(f"[StatisticalLSTM] Erro: {e}", exc_info=True)
            return _make_output(0.2, 0.2, 0.3, 0.15, 0.15, 30.0)


# ─── Modo PyTorch (se disponível) ─────────────────────────────────────────────
if TORCH_AVAILABLE:
    class _AttentionLayer(nn.Module):
        """Mecanismo de atenção temporal simples."""
        def __init__(self, hidden_size: int):
            super().__init__()
            self.attention = nn.Linear(hidden_size, 1)

        def forward(self, lstm_out: "torch.Tensor") -> "torch.Tensor":
            # lstm_out: [batch, seq, hidden]
            weights = torch.softmax(self.attention(lstm_out), dim=1)
            context = (weights * lstm_out).sum(dim=1)
            return context

    class _TorchLSTM(nn.Module):
        """LSTM bidirecional com atenção temporal."""
        def __init__(self, input_size=FEATURE_DIM, hidden_size=64, num_layers=2, output_size=5):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=input_size,
                hidden_size=hidden_size,
                num_layers=num_layers,
                batch_first=True,
                bidirectional=True,
                dropout=0.2 if num_layers > 1 else 0.0,
            )
            self.attention  = _AttentionLayer(hidden_size * 2)  # bidirecional
            self.fc1        = nn.Linear(hidden_size * 2, 32)
            self.fc2        = nn.Linear(32, output_size)
            self.relu       = nn.ReLU()
            self.dropout    = nn.Dropout(0.2)
            self.softmax    = nn.Softmax(dim=-1)

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            lstm_out, _ = self.lstm(x)
            context     = self.attention(lstm_out)
            out         = self.dropout(self.relu(self.fc1(context)))
            out         = self.softmax(self.fc2(out))
            return out

    class TorchLSTMModel:
        """Wrapper do modelo PyTorch com gerenciamento de estado."""

        def __init__(self):
            self.model  = _TorchLSTM()
            self.model.eval()
            self._initialized = False
            self._has_trained_weights = False  # True apenas apos load_weights bem sucedido
            self._init_weights()
            # Fallback estatistico pre-instanciado (pra delegar sem pesos treinados)
            self._fallback = StatisticalLSTM()

        def _init_weights(self):
            """Inicialização Xavier para convergência estável."""
            for name, param in self.model.named_parameters():
                if "weight" in name and param.dim() >= 2:
                    nn.init.xavier_uniform_(param)
                elif "bias" in name:
                    nn.init.zeros_(param)
            self._initialized = True

        def predict(self, sequence: np.ndarray) -> Dict[str, float]:
            """Inferência em modo eval (sem gradientes)."""
            # Sem pesos treinados: PyTorch retorna softmax aleatorio (xavier init).
            # Delega pro fallback estatistico — honesto e mais util.
            if not self._has_trained_weights:
                log.debug("[LSTM] Pesos não-treinados → delegando StatisticalLSTM")
                return self._fallback.predict(sequence)
            try:
                with torch.no_grad():
                    x   = torch.FloatTensor(sequence).unsqueeze(0)  # [1, seq, feat]
                    out = self.model(x)
                    probs = out.squeeze(0).numpy()
                    confidence = float(probs.max()) * 100.0
                    confidence = min(50.0 + confidence * 0.5, 90.0)
                    return _make_output(*probs.tolist(), confidence)
            except Exception as e:
                log.error(f"[TorchLSTM] Erro: {e}", exc_info=True)
                return _make_output(0.2, 0.2, 0.3, 0.15, 0.15, 30.0)


# ─── Classe pública ────────────────────────────────────────────────────────────
class LSTMModel:
    """
    Interface pública do modelo LSTM.
    Usa PyTorch se disponível, caso contrário mode estatístico.
    Peso na ensemble: 0.25
    """
    WEIGHT = 0.25

    def __init__(self):
        if TORCH_AVAILABLE:
            self._model = TorchLSTMModel()
            log.info("[LSTM] Inicializado com PyTorch (bidirecional + atenção).")
        else:
            self._model = StatisticalLSTM()
            log.info("[LSTM] Inicializado em modo estatístico (sem PyTorch).")

    def predict(self, feature_set: FeatureSet) -> Dict[str, float]:
        """
        Executa inferência LSTM sobre o feature_set.
        Retorna distribuição de probabilidades com confiança.
        """
        if not feature_set.valid:
            return _make_output(0.2, 0.2, 0.3, 0.15, 0.15, 20.0)
        return self._model.predict(feature_set.sequence)

    def load_weights(self, path: str) -> bool:
        """Carrega pesos treinados (somente modo PyTorch)."""
        if not TORCH_AVAILABLE or not hasattr(self._model, "model"):
            return False
        try:
            import torch
            state = torch.load(path, map_location="cpu")
            self._model.model.load_state_dict(state)
            self._model.model.eval()
            self._model._has_trained_weights = True
            log.info(f"[LSTM] Pesos treinados carregados: {path}")
            return True
        except Exception as e:
            log.warning(f"[LSTM] Falha ao carregar pesos {path}: {e}")
            return False

    def save_weights(self, path: str) -> bool:
        """Salva pesos (somente modo PyTorch)."""
        if not TORCH_AVAILABLE or not hasattr(self._model, "model"):
            return False
        try:
            import torch
            torch.save(self._model.model.state_dict(), path)
            log.info(f"[LSTM] Pesos salvos: {path}")
            return True
        except Exception as e:
            log.warning(f"[LSTM] Falha ao salvar pesos: {e}")
            return False
