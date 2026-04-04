"""
meta_learning_engine.py — Engine 10: Meta-Aprendizado Adaptativo
Analisa histórico de sinais institucionais para:
  1. Calcular performance relativa de cada engine
  2. Gerar pesos adaptativos para o Scoring Engine
  3. Produzir sinal direcional baseado em momentum histórico recente

Metodologia atual (Fase 1 — sem ground truth de P&L):
  - Usa convergência histórica (sinais de alta confluência como proxy de qualidade)
  - Analisa momentum direcional dos últimos N sinais
  - Versão futura: substituir por accuracy real com outcome tracking

Saída padrão:
  {
    "direction":      BULLISH | BEARISH | NEUTRAL,
    "score":          0-100   (50 = neutro),
    "confidence":     0-100,
    "engine_weights": {engine_id: weight},
    "signal_count":   int,
    "valid":          bool,
    "meta_score":     0-100,
    "accuracy_proxy": 0-100,
    "momentum_streak": int,  # positivo = bullish streak, negativo = bearish
  }
"""
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Configuração ─────────────────────────────────────────────────────────────
LOG_DIR            = Path("logs")
LOOKBACK_DAYS      = 14          # janela de análise histórica
MIN_SIGNALS        = 5           # mínimo para ativar ajuste adaptativo
MOMENTUM_WINDOW    = 7           # últimos N sinais para momentum direcional
ACCURACY_FLOOR     = 0.40        # acurácia mínima (abaixo → reduz peso do engine)
WEIGHT_SENSITIVITY = 0.25        # quanto o peso varia por performance (0 = sem ajuste)

# Pesos base — usados quando não há histórico suficiente
BASE_WEIGHTS: Dict[str, float] = {
    "smart_money":  0.15,
    "market_maker": 0.10,
    "liquidation":  0.05,
    "pressure":     0.10,
    "rare_setup":   0.08,
    "geopolitical": 0.07,
    "cycle":        0.10,
    "direction":    0.15,
    "neural":       0.15,
    "meta":         0.05,
}
assert abs(sum(BASE_WEIGHTS.values()) - 1.0) < 1e-9, "BASE_WEIGHTS devem somar 1.0"


# ── Leitura de logs ───────────────────────────────────────────────────────────

def _read_signal_log(days: int = LOOKBACK_DAYS) -> List[dict]:
    """Lê entradas de logs institucionais dos últimos N dias."""
    signals: List[dict] = []
    cutoff = datetime.now() - timedelta(days=days)

    if not LOG_DIR.exists():
        return signals

    for fpath in sorted(LOG_DIR.glob("institutional_*.jsonl")):
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                        ts_str = entry.get("timestamp", "")
                        if not ts_str:
                            continue
                        ts = datetime.fromisoformat(ts_str)
                        if ts >= cutoff:
                            signals.append(entry)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        continue
        except (OSError, IOError):
            continue

    return sorted(signals, key=lambda x: x.get("timestamp", ""))


# ── Momentum direcional ───────────────────────────────────────────────────────

def _compute_momentum(signals: List[dict]) -> Tuple[str, float, int]:
    """
    Analisa os últimos MOMENTUM_WINDOW sinais para detectar streak direcional.

    Returns:
        (direction, confidence, streak_length)
        streak > 0 = bullish streak, streak < 0 = bearish streak
    """
    if not signals:
        return "NEUTRAL", 0.0, 0

    recent = signals[-MOMENTUM_WINDOW:]
    directions = [s.get("direction", "NEUTRAL") for s in recent]

    bull_n = sum(1 for d in directions if d in ("LONG", "BULLISH"))
    bear_n = sum(1 for d in directions if d in ("SHORT", "BEARISH"))
    total  = len(directions)

    bull_pct = bull_n / total
    bear_pct = bear_n / total

    # Calcula streak corrente (quantos sinais consecutivos na mesma direção)
    streak   = 0
    last_dir = directions[-1] if directions else "NEUTRAL"
    for d in reversed(directions):
        if d == last_dir:
            streak += 1
        else:
            break

    streak_signed = streak if last_dir in ("LONG", "BULLISH") else -streak

    if bull_pct >= 0.65:
        direction  = "BULLISH"
        confidence = min(95.0, bull_pct * 100)
    elif bear_pct >= 0.65:
        direction  = "BEARISH"
        confidence = min(95.0, bear_pct * 100)
    else:
        direction  = "NEUTRAL"
        confidence = max(bull_pct, bear_pct) * 100

    return direction, confidence, streak_signed


# ── Score meta ────────────────────────────────────────────────────────────────

def _compute_meta_score(signals: List[dict], direction: str, streak: int) -> float:
    """
    Trinity Score meta [0-100] onde 50 = neutro.

    Fatores:
    - Média do inst_score dos sinais recentes
    - Intensidade do streak direcional
    - Consistência de confluências
    """
    if not signals:
        return 50.0

    recent     = signals[-MOMENTUM_WINDOW:]
    avg_score  = sum(s.get("inst_score", 50) for s in recent) / len(recent)
    avg_confl  = sum(s.get("confluences", 3) for s in recent) / len(recent)

    # Streak boost: cada sinal extra na mesma direção adiciona ~2 pontos
    streak_boost = min(abs(streak) * 2.0, 12.0)

    # Base: desvio do neutro proporcional ao avg_score
    base_deviation = (avg_score - 50.0) * 0.60

    # Confluence bonus: confluências altas indicam qualidade de sinal
    confl_bonus = max(0.0, (avg_confl - 3.0) * 1.5)

    raw_meta = 50.0 + base_deviation + confl_bonus

    if direction == "BULLISH":
        meta_score = raw_meta + streak_boost
    elif direction == "BEARISH":
        meta_score = raw_meta - streak_boost
    else:
        meta_score = 50.0 + (raw_meta - 50.0) * 0.3  # comprime ao neutro

    return max(0.0, min(100.0, meta_score))


# ── Acurácia proxy ────────────────────────────────────────────────────────────

def _compute_accuracy_proxy(signals: List[dict]) -> float:
    """
    Proxy de acurácia baseado em consistência de confluências.

    Lógica: sinais com ≥4 confluências e score ≥70 = "alta convicção"
    Proporção de sinais de alta convicção = proxy de acurácia do sistema.

    Versão futura: substituir por comparação preço+direção pós-sinal.
    """
    if len(signals) < 3:
        return 50.0

    high_conv = sum(
        1 for s in signals
        if s.get("confluences", 0) >= 4 and s.get("inst_score", 0) >= 68
    )
    proxy = (high_conv / len(signals)) * 100
    return round(max(30.0, min(95.0, proxy)), 1)


# ── Pesos adaptativos ─────────────────────────────────────────────────────────

def _compute_adaptive_weights(
    signals: List[dict],
    accuracy_proxy: float,
) -> Dict[str, float]:
    """
    Ajusta pesos dos engines baseado em performance histórica.

    Fase 1 (sem outcome tracking):
      - Engines de maior impacto (neural, direction, smc) mantêm peso base
      - Ajuste proporcional à acurácia proxy do sistema como um todo
      - Não ajusta engines individuais até ter outcome tracking real

    Quando accuracy_proxy < 50 → reduz peso de engines voláteis levemente.
    """
    if len(signals) < MIN_SIGNALS:
        return BASE_WEIGHTS.copy()

    weights = BASE_WEIGHTS.copy()

    # Ajuste global baseado em acurácia proxy
    if accuracy_proxy < 45.0:
        # Sistema em modo low-confidence: reduz engines de alta volatilidade
        volatile_engines = ["rare_setup", "liquidation"]
        stable_engines   = ["direction", "neural", "smart_money"]

        for eng in volatile_engines:
            weights[eng] = max(0.02, weights[eng] * (1.0 - WEIGHT_SENSITIVITY))
        for eng in stable_engines:
            weights[eng] = min(0.25, weights[eng] * (1.0 + WEIGHT_SENSITIVITY * 0.5))

    elif accuracy_proxy >= 75.0:
        # Sistema em modo high-confidence: amplifica sinal direcional
        directional_engines = ["direction", "neural", "smart_money", "pressure"]
        for eng in directional_engines:
            weights[eng] = min(0.25, weights[eng] * (1.0 + WEIGHT_SENSITIVITY * 0.3))

    # Renormaliza para somar 1.0
    total = sum(weights.values())
    if total > 0:
        weights = {k: round(v / total, 4) for k, v in weights.items()}

    # Corrige arredondamento no engine de maior peso
    diff = 1.0 - sum(weights.values())
    if abs(diff) > 1e-9:
        top_engine = max(weights, key=weights.get)
        weights[top_engine] = round(weights[top_engine] + diff, 4)

    return weights


# ── Ponto de entrada público ──────────────────────────────────────────────────

def run_meta_analysis() -> dict:
    """
    Ponto de entrada público do Meta Learning Engine (Engine 10).

    Returns:
        dict compatível com EngineResult do Engine Orchestrator:
        {
          "direction":       BULLISH | BEARISH | NEUTRAL,
          "score":           0-100,
          "confidence":      0-100,
          "engine_weights":  {engine_id: float},
          "signal_count":    int,
          "valid":           bool,
          "meta_score":      float,
          "accuracy_proxy":  float,
          "momentum_streak": int,
        }
    """
    t0 = time.monotonic()
    try:
        signals        = _read_signal_log()
        count          = len(signals)
        direction, conf, streak = _compute_momentum(signals)
        meta_score     = _compute_meta_score(signals, direction, streak)
        accuracy_proxy = _compute_accuracy_proxy(signals)
        weights        = _compute_adaptive_weights(signals, accuracy_proxy)

        elapsed = (time.monotonic() - t0) * 1000
        log.debug(
            f"   [Meta] sinais={count} | dir={direction} | score={meta_score:.1f} "
            f"| conf={conf:.0f}% | streak={streak:+d} | acc_proxy={accuracy_proxy:.0f}% "
            f"| {elapsed:.0f}ms"
        )

        return {
            "direction":       direction,
            "score":           meta_score,
            "confidence":      conf,
            "engine_weights":  weights,
            "signal_count":    count,
            "valid":           count >= MIN_SIGNALS,
            "meta_score":      meta_score,
            "accuracy_proxy":  accuracy_proxy,
            "momentum_streak": streak,
        }

    except Exception as exc:
        log.warning(f"[MetaLearning] erro inesperado: {exc}")
        return {
            "direction":       "NEUTRAL",
            "score":           50.0,
            "confidence":      0.0,
            "engine_weights":  BASE_WEIGHTS.copy(),
            "signal_count":    0,
            "valid":           False,
            "meta_score":      50.0,
            "accuracy_proxy":  50.0,
            "momentum_streak": 0,
        }
