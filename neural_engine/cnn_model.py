"""
cnn_model.py — 1D CNN Chart Pattern Detector
Cap. 17 | Trinity Neural Intelligence Engine v2

CNN 1D para detecção de padrões gráficos clássicos em séries temporais de preço.
Captura estruturas locais (padrões de velas, figuras chartistas, compressão/expansão).

Padrões detectados estatisticamente:
  - Head & Shoulders / Inverse H&S
  - Double Top / Double Bottom
  - Bull/Bear Flags e Pennants
  - Ascending/Descending Wedges
  - Bollinger Squeeze e Expansão
  - Vela Engulfing / Hammer / Shooting Star

Modo PyTorch (se disponível):
  - 3 camadas Conv1D com kernel sizes [3, 7, 15]
  - Multi-scale feature extraction + Global Average Pooling

Peso na ensemble: 0.20
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional, List, Tuple

from .feature_engine import FeatureSet, SEQUENCE_LEN, FEATURE_DIM, _safe

log = logging.getLogger(__name__)

# ─── Tentativa de import PyTorch ───────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
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
class StatisticalCNN:
    """
    Detector de padrões gráficos via análise estatística de séries temporais.
    Opera sobre sequências de closes, highs, lows e volumes.
    """

    def predict(self, feature_set: FeatureSet) -> Dict[str, float]:
        try:
            seq = feature_set.sequence  # [SEQUENCE_LEN, FEATURE_DIM]

            # Extrair colunas de interesse
            returns   = seq[:, 0]   # retorno log
            body_rat  = seq[:, 1]   # body ratio
            wick_up   = seq[:, 2]   # sombra superior
            wick_dn   = seq[:, 3]   # sombra inferior
            clv       = seq[:, 4]   # CLV
            vol_real  = seq[:, 6]   # volatilidade realizada
            vol_norm  = seq[:, 7]   # volume normalizado
            bb_pos    = seq[:, 9]   # posição BB
            rsi_norm  = seq[:, 10]  # RSI
            ema_cross = seq[:, 11]  # EMA cross

            n = len(returns)
            bull_score = 0.0
            bear_score = 0.0
            side_score = 0.0
            vexp_score = 0.0
            manip_score = 0.0
            pattern_count = 0

            # ── Padrão 1: Double Bottom (bullish reversal) ────────────────────
            if n >= 30:
                db_score = self._detect_double_bottom(returns, rsi_norm)
                bull_score += db_score * 0.3
                if db_score > 0.5: pattern_count += 1

            # ── Padrão 2: Double Top (bearish reversal) ────────────────────────
            if n >= 30:
                dt_score = self._detect_double_top(returns, rsi_norm)
                bear_score += dt_score * 0.3
                if dt_score > 0.5: pattern_count += 1

            # ── Padrão 3: Head & Shoulders ─────────────────────────────────────
            if n >= 40:
                hs_bull, hs_bear = self._detect_head_shoulders(returns)
                bull_score += hs_bull * 0.25
                bear_score += hs_bear * 0.25
                if max(hs_bull, hs_bear) > 0.5: pattern_count += 1

            # ── Padrão 4: Flag / Pennant (tendência contínua) ─────────────────
            if n >= 20:
                flag_bull, flag_bear = self._detect_flag(returns, vol_norm)
                bull_score += flag_bull * 0.25
                bear_score += flag_bear * 0.25
                if max(flag_bull, flag_bear) > 0.5: pattern_count += 1

            # ── Padrão 5: Bollinger Squeeze → Expansão ────────────────────────
            if n >= 20:
                squeeze_score = self._detect_bb_squeeze(vol_real, bb_pos)
                vexp_score += squeeze_score * 0.4
                if squeeze_score > 0.5: pattern_count += 1

            # ── Padrão 6: Padrões de vela (Engulfing, Hammer, etc.) ───────────
            if n >= 5:
                candle_bull, candle_bear, candle_manip = self._detect_candle_patterns(
                    returns, body_rat, wick_up, wick_dn, clv
                )
                bull_score  += candle_bull  * 0.20
                bear_score  += candle_bear  * 0.20
                manip_score += candle_manip * 0.25
                if max(candle_bull, candle_bear) > 0.5: pattern_count += 1

            # ── Padrão 7: Wedge (cunha ascendente/descendente) ────────────────
            if n >= 20:
                wedge_bull, wedge_bear = self._detect_wedge(returns, vol_norm)
                bull_score += wedge_bull * 0.20
                bear_score += wedge_bear * 0.20

            # ── Sideway: Bollinger Band centralized + RSI central ─────────────
            if n >= 10:
                bb_center = np.abs(bb_pos[-10:]).mean() < 0.25
                rsi_center = np.abs(rsi_norm[-10:] - 0.5).mean() < 0.1
                if bb_center and rsi_center:
                    side_score += 0.40

            # ── Normalização: se nenhum padrão claro, favorecer sideways ─────
            total = bull_score + bear_score + side_score + vexp_score + manip_score
            if total < 0.3:
                side_score += 0.35

            # ── Confiança: baseada no número de padrões detectados ────────────
            confidence = 30.0 + pattern_count * 12.0
            confidence = min(confidence, 85.0)

            return _make_output(bull_score, bear_score, side_score, vexp_score, manip_score, confidence)

        except Exception as e:
            log.error(f"[StatisticalCNN] Erro: {e}", exc_info=True)
            return _make_output(0.2, 0.2, 0.3, 0.15, 0.15, 30.0)

    # ── Detectores de padrão ──────────────────────────────────────────────────

    def _detect_double_bottom(self, returns: np.ndarray, rsi: np.ndarray) -> float:
        """Detecta Double Bottom: dois mínimos próximos com RSI divergente."""
        try:
            n = len(returns)
            if n < 30: return 0.0
            cum_ret = np.cumsum(returns)
            # Dois vales na janela de 30 barras
            mid = n // 2
            first_low  = cum_ret[max(0, mid-15):mid].min()
            second_low = cum_ret[mid:min(n, mid+15)].min()
            mid_high   = cum_ret[mid-5:mid+5].max()
            # Double bottom: dois vales similares, pico no meio, RSI divergente
            lows_similar = abs(first_low - second_low) < abs(first_low) * 0.05
            peak_valid   = mid_high > max(first_low, second_low) * 0.5
            rsi_div = rsi[-1] > rsi[-15] if n >= 15 else False
            score = 0.0
            if lows_similar: score += 0.5
            if peak_valid:   score += 0.3
            if rsi_div:      score += 0.2
            return score
        except: return 0.0

    def _detect_double_top(self, returns: np.ndarray, rsi: np.ndarray) -> float:
        """Detecta Double Top: dois máximos próximos com RSI divergente."""
        try:
            n = len(returns)
            if n < 30: return 0.0
            cum_ret = np.cumsum(returns)
            mid = n // 2
            first_high  = cum_ret[max(0, mid-15):mid].max()
            second_high = cum_ret[mid:min(n, mid+15)].max()
            mid_low     = cum_ret[mid-5:mid+5].min()
            highs_similar = abs(first_high - second_high) < abs(first_high) * 0.05
            trough_valid  = mid_low < min(first_high, second_high) * 0.5
            rsi_div = rsi[-1] < rsi[-15] if n >= 15 else False
            score = 0.0
            if highs_similar: score += 0.5
            if trough_valid:  score += 0.3
            if rsi_div:       score += 0.2
            return score
        except: return 0.0

    def _detect_head_shoulders(self, returns: np.ndarray) -> Tuple[float, float]:
        """Detecta Head & Shoulders (bearish) e Inverse H&S (bullish)."""
        try:
            n = len(returns)
            if n < 40: return 0.0, 0.0
            cum_ret = np.cumsum(returns)
            seg = n // 4
            s1 = cum_ret[0:seg].max()
            hd = cum_ret[seg:2*seg].max()
            s2 = cum_ret[2*seg:3*seg].max()
            # H&S bearish: head > both shoulders, shoulders similar
            shoulders_similar = abs(s1 - s2) < abs(s1) * 0.1
            head_higher = hd > max(s1, s2)
            hs_bear = 0.0
            if shoulders_similar and head_higher:
                hs_bear = 0.6 + (0.4 if cum_ret[-1] < cum_ret[2*seg] else 0)

            # Inverse H&S (bullish): troughs
            s1_lo = cum_ret[0:seg].min()
            hd_lo = cum_ret[seg:2*seg].min()
            s2_lo = cum_ret[2*seg:3*seg].min()
            sh_sim = abs(s1_lo - s2_lo) < abs(s1_lo) * 0.1 if abs(s1_lo) > 1e-8 else False
            hd_lower = hd_lo < min(s1_lo, s2_lo)
            hs_bull = 0.0
            if sh_sim and hd_lower:
                hs_bull = 0.6 + (0.4 if cum_ret[-1] > cum_ret[2*seg] else 0)

            return hs_bull, hs_bear
        except: return 0.0, 0.0

    def _detect_flag(self, returns: np.ndarray, vol_norm: np.ndarray) -> Tuple[float, float]:
        """Detecta Bull/Bear Flag: forte movimento seguido de consolidação."""
        try:
            n = len(returns)
            if n < 20: return 0.0, 0.0
            # Fase de impulso: primeiras 10 barras
            impulse = returns[:10].mean()
            # Fase de consolidação: últimas 10 barras
            consol  = returns[-10:]
            consol_std  = consol.std()
            consol_mean = consol.mean()
            vol_fade = vol_norm[-10:].mean() < vol_norm[:10].mean()  # volume caindo na consolidação
            # Bull flag: impulso positivo + consolidação + volume caindo
            bull_flag = 0.0
            if impulse > 0.05 and abs(consol_mean) < 0.01 and vol_fade:
                bull_flag = 0.7
            elif impulse > 0.03 and abs(consol_mean) < 0.02:
                bull_flag = 0.4
            # Bear flag: impulso negativo + consolidação + volume caindo
            bear_flag = 0.0
            if impulse < -0.05 and abs(consol_mean) < 0.01 and vol_fade:
                bear_flag = 0.7
            elif impulse < -0.03 and abs(consol_mean) < 0.02:
                bear_flag = 0.4
            return bull_flag, bear_flag
        except: return 0.0, 0.0

    def _detect_bb_squeeze(self, vol_real: np.ndarray, bb_pos: np.ndarray) -> float:
        """Detecta Bollinger Squeeze seguido de expansão."""
        try:
            n = len(vol_real)
            if n < 20: return 0.0
            # Volatilidade baixa nas últimas 10 barras (squeeze)
            vol_recent = vol_real[-10:].mean() if n >= 10 else vol_real.mean()
            vol_hist   = vol_real.mean()
            # BB posição próxima do centro = compressão
            bb_compressed = np.abs(bb_pos[-10:]).mean() < 0.2 if n >= 10 else False
            squeeze_intensity = max(0, 1.0 - vol_recent / (vol_hist + 1e-8))
            score = 0.0
            if squeeze_intensity > 0.4: score += 0.4
            if bb_compressed: score += 0.4
            if score > 0.4:
                # Verifica se está expandindo recentemente
                expanding = vol_real[-3:].mean() > vol_real[-10:-3].mean() if n >= 10 else False
                if expanding: score = min(score + 0.2, 1.0)
            return score
        except: return 0.0

    def _detect_candle_patterns(
        self, returns, body_rat, wick_up, wick_dn, clv
    ) -> Tuple[float, float, float]:
        """Detecta padrões de velas: Hammer, Shooting Star, Engulfing, Doji."""
        try:
            n = len(returns)
            bull_p = bear_p = manip_p = 0.0
            k = min(5, n)

            for i in range(-k, 0):
                br = body_rat[i] if abs(i) <= n else 0.5
                wu = wick_up[i]  if abs(i) <= n else 0.0
                wd = wick_dn[i]  if abs(i) <= n else 0.0
                c  = clv[i]      if abs(i) <= n else 0.0
                r  = returns[i]  if abs(i) <= n else 0.0

                # Hammer (bullish): corpo pequeno no topo, sombra inferior longa
                if wu < 0.15 and wd > 0.55 and br < 0.3:
                    bull_p += 0.20

                # Shooting Star (bearish): corpo pequeno na base, sombra superior longa
                if wd < 0.15 and wu > 0.55 and br < 0.3:
                    bear_p += 0.20

                # Bullish Engulfing (corpo grande verde após corpo vermelho)
                if br > 0.70 and r > 0 and c > 0.5:
                    bull_p += 0.15

                # Bearish Engulfing (corpo grande vermelho após corpo verde)
                if br > 0.70 and r < 0 and c < -0.5:
                    bear_p += 0.15

                # Doji (corpo muito pequeno = indecisão / possível manipulação)
                if br < 0.10 and (wu + wd) > 0.60:
                    manip_p += 0.20

            # Normaliza pelo número de velas analisadas
            bull_p  = min(bull_p, 1.0)
            bear_p  = min(bear_p, 1.0)
            manip_p = min(manip_p, 1.0)
            return bull_p, bear_p, manip_p
        except: return 0.0, 0.0, 0.0

    def _detect_wedge(self, returns: np.ndarray, vol_norm: np.ndarray) -> Tuple[float, float]:
        """Detecta Wedge (cunha) ascendente/descendente."""
        try:
            n = len(returns)
            if n < 20: return 0.0, 0.0
            cum_ret = np.cumsum(returns[-20:])
            # Slope dos highs e lows
            highs = np.array([cum_ret[i] for i in range(0, 20, 4)]).max() if n >= 20 else 0
            # Ascending wedge (bearish): altas e baixas crescendo, mas comprimindo
            slope = np.polyfit(np.arange(20), cum_ret, 1)[0]
            std_vals = cum_ret.std()
            vol_fade = vol_norm[-10:].mean() < vol_norm[-20:-10].mean() if n >= 20 else False
            bull_wedge = bear_wedge = 0.0
            if slope < 0 and vol_fade:
                bull_wedge = 0.5  # descending wedge = bullish reversal iminente
            if slope > 0 and vol_fade:
                bear_wedge = 0.5  # ascending wedge = bearish reversal iminente
            return bull_wedge, bear_wedge
        except: return 0.0, 0.0


# ─── Modo PyTorch ─────────────────────────────────────────────────────────────
if TORCH_AVAILABLE:
    class _TorchCNN(nn.Module):
        """CNN multi-escala para detecção de padrões em séries temporais."""
        def __init__(self, in_channels=FEATURE_DIM, output_size=5):
            super().__init__()
            # Três escalas: curta (3), média (7), longa (15)
            self.conv_short  = nn.Conv1d(in_channels, 32, kernel_size=3,  padding=1)
            self.conv_medium = nn.Conv1d(in_channels, 32, kernel_size=7,  padding=3)
            self.conv_long   = nn.Conv1d(in_channels, 32, kernel_size=15, padding=7)
            self.bn1  = nn.BatchNorm1d(32)
            self.bn2  = nn.BatchNorm1d(32)
            self.bn3  = nn.BatchNorm1d(32)
            self.pool = nn.AdaptiveAvgPool1d(1)
            self.fc   = nn.Sequential(
                nn.Linear(96, 48),
                nn.ReLU(),
                nn.Dropout(0.2),
                nn.Linear(48, output_size),
                nn.Softmax(dim=-1),
            )

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            # x: [1, SEQUENCE_LEN, FEATURE_DIM] → transpõe para [1, FEATURE_DIM, SEQUENCE_LEN]
            x = x.permute(0, 2, 1)
            s = F.relu(self.bn1(self.conv_short(x)))
            m = F.relu(self.bn2(self.conv_medium(x)))
            l = F.relu(self.bn3(self.conv_long(x)))
            # Global Average Pool
            s = self.pool(s).squeeze(-1)
            m = self.pool(m).squeeze(-1)
            l = self.pool(l).squeeze(-1)
            combined = torch.cat([s, m, l], dim=-1)
            return self.fc(combined)

    class TorchCNNModel:
        def __init__(self):
            self.model = _TorchCNN()
            self.model.eval()
            for name, p in self.model.named_parameters():
                if "weight" in name and p.dim() >= 2:
                    nn.init.kaiming_normal_(p)
                elif "bias" in name:
                    nn.init.zeros_(p)

        def predict(self, feature_set: FeatureSet) -> Dict[str, float]:
            try:
                with torch.no_grad():
                    x   = torch.FloatTensor(feature_set.sequence).unsqueeze(0)
                    out = self.model(x)
                    probs = out.squeeze(0).numpy()
                    confidence = float(probs.max()) * 100.0
                    confidence = min(45.0 + confidence * 0.5, 88.0)
                    return _make_output(*probs.tolist(), confidence)
            except Exception as e:
                log.error(f"[TorchCNN] Erro: {e}", exc_info=True)
                return _make_output(0.2, 0.2, 0.3, 0.15, 0.15, 30.0)


# ─── Classe pública ────────────────────────────────────────────────────────────
class CNNModel:
    """
    Interface pública do modelo CNN 1D.
    Peso na ensemble: 0.20
    """
    WEIGHT = 0.20

    def __init__(self):
        if TORCH_AVAILABLE:
            self._model = TorchCNNModel()
            log.info("[CNN] Inicializado com PyTorch (multi-scale 1D conv).")
        else:
            self._model = StatisticalCNN()
            log.info("[CNN] Inicializado em modo estatístico (detector de padrões).")

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
            log.warning(f"[CNN] Falha ao carregar pesos: {e}")
            return False

    def save_weights(self, path: str) -> bool:
        if not TORCH_AVAILABLE or not hasattr(self._model, "model"):
            return False
        try:
            import torch
            torch.save(self._model.model.state_dict(), path)
            return True
        except Exception as e:
            log.warning(f"[CNN] Falha ao salvar pesos: {e}")
            return False
