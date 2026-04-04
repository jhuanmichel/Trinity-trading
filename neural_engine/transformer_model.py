"""
transformer_model.py — Multi-Timeframe Transformer Intelligence
Cap. 17 | Trinity Neural Intelligence Engine v2

Modelo Transformer com mecanismo de atenção multi-cabeça para correlação
cruzada entre timeframes (1D, 4H, 1H, 15m).

Arquitetura (modo estatístico — sem PyTorch):
  - Atenção multi-timeframe via correlação estatística
  - Pesos de atenção por importância de timeframe
  - Output: distribuição de 5 probabilidades

Modo PyTorch (se disponível):
  - Encoder Transformer completo com positional encoding
  - Multi-head attention [4 cabeças] sobre sequência + MTF matrix

Peso na ensemble: 0.25
"""

import logging
import numpy as np
from typing import Dict, Optional

from .feature_engine import FeatureSet, SEQUENCE_LEN, FEATURE_DIM, MTF_TIMEFRAMES, _safe

log = logging.getLogger(__name__)

# ─── Tentativa de import PyTorch ───────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn
    import math
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
class StatisticalTransformer:
    """
    Aproximação estatística do Transformer multi-timeframe.
    Usa correlação cruzada entre timeframes e análise de consistência de sinal.
    """

    # Pesos de atenção por timeframe (1D mais importante para direção macro)
    TF_WEIGHTS = {0: 0.40, 1: 0.30, 2: 0.20, 3: 0.10}  # 1D, 4H, 1H, 15m

    def predict(self, feature_set: FeatureSet) -> Dict[str, float]:
        try:
            seq = feature_set.sequence    # [SEQUENCE_LEN, FEATURE_DIM]
            mtf = feature_set.mtf_matrix  # [MTF_TIMEFRAMES, FEATURE_DIM]

            # ── Análise da sequência temporal ─────────────────────────────────
            returns  = seq[:, 0]  # retorno log
            clv      = seq[:, 4]  # close location value
            momentum = seq[:, 5]  # momentum normalizado
            rsi      = seq[:, 10] # RSI normalizado
            ema      = seq[:, 11] # EMA cross

            # Atenção temporal: pesos para barras mais recentes (decaimento exponencial)
            n = len(returns)
            time_weights = np.exp(np.linspace(-2, 0, n))
            time_weights /= time_weights.sum()

            # Retorno ponderado temporalmente
            weighted_ret = np.dot(time_weights, returns)
            weighted_clv = np.dot(time_weights, clv)
            weighted_mom = np.dot(time_weights, momentum)

            # ── Análise MTF (atenção cruzada entre timeframes) ────────────────
            # SMC scores por TF [0=1D, 1=4H, 2=1H, 3=15m]
            mtf_bull = 0.0
            mtf_bear = 0.0
            mtf_side = 0.0
            mtf_conf = 0.0

            for i in range(MTF_TIMEFRAMES):
                tf_w = self.TF_WEIGHTS.get(i, 0.1)
                tf_row = mtf[i]
                smc_score = _safe(tf_row[0])  # col 0 = smc_score / 100
                struct    = _safe(tf_row[1])  # col 1 = structure / 100
                liq       = _safe(tf_row[2])  # col 2 = liquidity / 100
                bos       = _safe(tf_row[3])  # col 3 = bos / 100
                choch     = _safe(tf_row[4])  # col 4 = choch / 100
                ob        = _safe(tf_row[5])  # col 5 = order_block / 100
                fvg       = _safe(tf_row[6])  # col 6 = fvg / 100
                mom_tf    = _safe(tf_row[7])  # col 7 = momentum / 100

                # CHOCH = sinal de reversão → favorece bull/bear dependendo de contexto
                reversal = choch

                # Score bullish por TF
                bull_tf = (struct * 0.3 + smc_score * 0.2 + bos * 0.2 + ob * 0.2 + mom_tf * 0.1)
                bear_tf = 1.0 - bull_tf  # aproximação simétrica
                side_tf = max(0, 1.0 - abs(bull_tf - 0.5) * 3.0)  # alta quando neutro

                mtf_bull += bull_tf * tf_w
                mtf_bear += bear_tf * tf_w
                mtf_side += side_tf * tf_w
                mtf_conf += smc_score * tf_w

            # Normalização
            mtf_total = mtf_bull + mtf_bear + mtf_side + 1e-8
            mtf_bull /= mtf_total
            mtf_bear /= mtf_total
            mtf_side /= mtf_total

            # ── Combinação: sequência temporal + MTF ─────────────────────────
            # Sequência (60% peso)
            seq_bull = 0.0
            seq_bear = 0.0
            seq_side = 0.0

            if weighted_ret > 0.05:   seq_bull += 0.30
            elif weighted_ret < -0.05: seq_bear += 0.30
            else:                      seq_side += 0.30

            if weighted_clv > 0.15:   seq_bull += 0.25
            elif weighted_clv < -0.15: seq_bear += 0.25
            else:                      seq_side += 0.15

            if weighted_mom > 0.10:   seq_bull += 0.20
            elif weighted_mom < -0.10: seq_bear += 0.20
            else:                      seq_side += 0.15

            rsi_last = rsi[-1] if n > 0 else 0.5
            if rsi_last > 0.65: seq_bear += 0.10  # sobrecomprado → bear pressure
            elif rsi_last < 0.35: seq_bull += 0.10
            else: seq_side += 0.05

            ema_last = ema[-1] if n > 0 else 0
            if ema_last > 0.001: seq_bull += 0.15
            elif ema_last < -0.001: seq_bear += 0.15
            else: seq_side += 0.10

            seq_total = seq_bull + seq_bear + seq_side + 1e-8
            seq_bull /= seq_total
            seq_bear /= seq_total
            seq_side /= seq_total

            # Combinação MTF + sequência
            final_bull = mtf_bull * 0.45 + seq_bull * 0.55
            final_bear = mtf_bear * 0.45 + seq_bear * 0.55
            final_side = mtf_side * 0.45 + seq_side * 0.55

            # ── Volatility Expansion: detecta divergência entre TFs ─────────
            vexp_score = 0.0
            vol_feat = seq[:, 6]  # volatilidade realizada
            if len(vol_feat) >= 8:
                vol_ratio = vol_feat[-4:].mean() / (vol_feat[-20:].mean() + 1e-8) if len(vol_feat) >= 20 else 1.0
                if vol_ratio > 1.8: vexp_score = 0.25
                elif vol_ratio > 1.3: vexp_score = 0.12

            # ── Manipulation: inconsistência MTF + vol anormal ────────────────
            manip_score = 0.0
            tf_agreement = np.std([mtf_bull, 1.0 - mtf_bear, mtf_side]) * 2
            if tf_agreement > 0.4: manip_score = 0.15

            # ── Confiança baseada na consistência MTF ─────────────────────────
            dominant = max(final_bull, final_bear, final_side)
            spread   = sorted([final_bull, final_bear, final_side], reverse=True)
            spread_val = spread[0] - spread[1] if len(spread) > 1 else 0
            mtf_consistency = 1.0 - tf_agreement  # alta consistência = alta confiança
            confidence = 35.0 + spread_val * 80.0 + mtf_consistency * 25.0 + mtf_conf * 20.0
            confidence = min(confidence, 92.0)

            return _make_output(final_bull, final_bear, final_side, vexp_score, manip_score, confidence)

        except Exception as e:
            log.error(f"[StatisticalTransformer] Erro: {e}", exc_info=True)
            return _make_output(0.2, 0.2, 0.3, 0.15, 0.15, 30.0)


# ─── Modo PyTorch ─────────────────────────────────────────────────────────────
if TORCH_AVAILABLE:
    class _PositionalEncoding(nn.Module):
        def __init__(self, d_model: int, max_len: int = 512, dropout: float = 0.1):
            super().__init__()
            self.dropout = nn.Dropout(dropout)
            pe = torch.zeros(max_len, d_model)
            position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
            div_term = torch.exp(
                torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model)
            )
            pe[:, 0::2] = torch.sin(position * div_term)
            pe[:, 1::2] = torch.cos(position * div_term)
            pe = pe.unsqueeze(0)
            self.register_buffer("pe", pe)

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            x = x + self.pe[:, : x.size(1), :]
            return self.dropout(x)

    class _TorchTransformer(nn.Module):
        """Encoder Transformer com atenção multi-cabeça."""
        def __init__(self, feature_dim=FEATURE_DIM, d_model=64, nhead=4, num_layers=2, output_size=5):
            super().__init__()
            self.input_proj   = nn.Linear(feature_dim, d_model)
            self.pos_enc      = _PositionalEncoding(d_model, max_len=SEQUENCE_LEN + MTF_TIMEFRAMES + 4)
            encoder_layer     = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=nhead, dim_feedforward=128,
                dropout=0.1, batch_first=True
            )
            self.encoder      = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
            self.pool         = nn.AdaptiveAvgPool1d(1)
            self.fc           = nn.Linear(d_model, output_size)
            self.softmax      = nn.Softmax(dim=-1)

        def forward(self, seq: "torch.Tensor", mtf: "torch.Tensor") -> "torch.Tensor":
            # seq: [1, SEQUENCE_LEN, FEATURE_DIM]
            # mtf: [1, MTF_TIMEFRAMES, FEATURE_DIM]
            combined = torch.cat([seq, mtf], dim=1)        # [1, seq+mtf, feat]
            x = self.input_proj(combined)                  # [1, seq+mtf, d_model]
            x = self.pos_enc(x)
            x = self.encoder(x)                            # [1, seq+mtf, d_model]
            # Pool over sequence dim
            x = x.permute(0, 2, 1)                         # [1, d_model, seq+mtf]
            x = self.pool(x).squeeze(-1)                   # [1, d_model]
            out = self.softmax(self.fc(x))                  # [1, output_size]
            return out

    class TorchTransformerModel:
        def __init__(self):
            self.model = _TorchTransformer()
            self.model.eval()
            for name, p in self.model.named_parameters():
                if "weight" in name and p.dim() >= 2:
                    nn.init.xavier_uniform_(p)
                elif "bias" in name:
                    nn.init.zeros_(p)

        def predict(self, feature_set: FeatureSet) -> Dict[str, float]:
            try:
                with torch.no_grad():
                    seq = torch.FloatTensor(feature_set.sequence).unsqueeze(0)
                    mtf = torch.FloatTensor(feature_set.mtf_matrix).unsqueeze(0)
                    out = self.model(seq, mtf)
                    probs = out.squeeze(0).numpy()
                    confidence = float(probs.max()) * 100.0
                    confidence = min(50.0 + confidence * 0.5, 92.0)
                    return _make_output(*probs.tolist(), confidence)
            except Exception as e:
                log.error(f"[TorchTransformer] Erro: {e}", exc_info=True)
                return _make_output(0.2, 0.2, 0.3, 0.15, 0.15, 30.0)


# ─── Classe pública ────────────────────────────────────────────────────────────
class TransformerModel:
    """
    Interface pública do modelo Transformer multi-timeframe.
    Peso na ensemble: 0.25
    """
    WEIGHT = 0.25

    def __init__(self):
        if TORCH_AVAILABLE:
            self._model = TorchTransformerModel()
            log.info("[Transformer] Inicializado com PyTorch (multi-head attention).")
        else:
            self._model = StatisticalTransformer()
            log.info("[Transformer] Inicializado em modo estatístico.")

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
            return True
        except Exception as e:
            log.warning(f"[Transformer] Falha ao carregar pesos: {e}")
            return False

    def save_weights(self, path: str) -> bool:
        if not TORCH_AVAILABLE or not hasattr(self._model, "model"):
            return False
        try:
            import torch
            torch.save(self._model.model.state_dict(), path)
            return True
        except Exception as e:
            log.warning(f"[Transformer] Falha ao salvar pesos: {e}")
            return False
