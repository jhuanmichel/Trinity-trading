"""
conflict_detector.py — Detector de Conflitos entre Engines

Detecta e classifica conflitos quando engines retornam sinais contraditórios.
Um conflito alto indica mercado ambíguo — trades devem ser evitados.

Três níveis de conflito:
  LOW:    dominant > 70%  → mercado com direção clara, trade permitido
  MEDIUM: dominant 55-70% → ambiguidade moderada, reduzir tamanho
  HIGH:   dominant < 55%  → conflito severo, sem trade

Conflitos específicos monitorados:
  - Neural BULLISH + Direction BEARISH (ou vice-versa)
  - SMC + Market Maker em lados opostos
  - Pressure vs Cycle em divergência
  - Geopolitical vs Neural em contradição

Saída:
  {
    "conflict":          bool,
    "conflict_level":    LOW | MEDIUM | HIGH,
    "trade_allowed":     bool,
    "conflict_score":    0-100  (0 = sem conflito, 100 = conflito máximo),
    "trade_size_factor": 0-1.0  (multiplicador de posição sugerido),
    "specific_conflicts":[{pair, severity, description}],
    "dominant_pct":      float,
    "resolution_hint":   str,
  }
"""
import logging
from typing import Dict, List, Optional

from trinity.core.engine_orchestrator import EngineResult

log = logging.getLogger(__name__)

# Limiares de conflito
CONFLICT_HIGH_THR   = 0.55  # dominant < 55% → HIGH
CONFLICT_MEDIUM_THR = 0.70  # dominant < 70% → MEDIUM
# dominant >= 70% → LOW

# Pares de engines críticos para conflito específico
CRITICAL_PAIRS = [
    ("neural",       "direction",    "HIGH",   "Neural vs Direction divergência"),
    ("smart_money",  "market_maker", "MEDIUM", "SMC vs Market Maker contraditório"),
    ("pressure",     "cycle",        "MEDIUM", "Pressão vs Ciclo em divergência"),
    ("geopolitical", "neural",       "LOW",    "Geo vs Neural divergência macro"),
    ("direction",    "smart_money",  "HIGH",   "Direction vs SMC opostos"),
]


def detect_conflict(
    engine_results: Dict[str, EngineResult],
    consensus_data: dict,
) -> dict:
    """
    Detecta e classifica conflitos entre engines.

    Args:
        engine_results: dict[engine_id, EngineResult] do EngineOrchestrator
        consensus_data: saída do SignalConsensus

    Returns:
        dict completo de conflito (ver docstring do módulo)
    """
    dominant_pct = consensus_data.get("dominant_pct", 50.0)
    dominant_ratio = dominant_pct / 100.0

    # ── Nível de conflito primário (baseado em votação) ───────────────────────
    if dominant_ratio >= CONFLICT_MEDIUM_THR:
        conflict_level = "LOW"
    elif dominant_ratio >= CONFLICT_HIGH_THR:
        conflict_level = "MEDIUM"
    else:
        conflict_level = "HIGH"

    # ── Conflitos específicos por par ─────────────────────────────────────────
    specific_conflicts = _detect_pair_conflicts(engine_results)

    # Eleva nível de conflito se há pares críticos opostos
    has_high_pair = any(c["severity"] == "HIGH" for c in specific_conflicts)
    if has_high_pair and conflict_level == "LOW":
        conflict_level = "MEDIUM"
    elif has_high_pair and conflict_level == "MEDIUM":
        conflict_level = "HIGH"

    # ── Score de conflito [0-100] ─────────────────────────────────────────────
    # 0 = perfeito alinhamento, 100 = máxima contradição
    base_conflict = max(0.0, (1.0 - dominant_ratio) * 100.0)
    pair_penalty  = len(specific_conflicts) * 5.0
    high_penalty  = sum(8.0 for c in specific_conflicts if c["severity"] == "HIGH")
    conflict_score = min(100.0, base_conflict + pair_penalty + high_penalty)

    # ── Trade permitido ───────────────────────────────────────────────────────
    trade_allowed = conflict_level != "HIGH"

    # ── Fator de tamanho de posição ───────────────────────────────────────────
    size_map = {"LOW": 1.0, "MEDIUM": 0.65, "HIGH": 0.0}
    trade_size_factor = size_map[conflict_level]

    # ── Hint de resolução ─────────────────────────────────────────────────────
    resolution_hint = _build_resolution_hint(
        conflict_level, dominant_pct, specific_conflicts,
        consensus_data.get("consensus", "NEUTRAL"),
    )

    is_conflict = conflict_level != "LOW"

    log.debug(
        f"   [Conflict] level={conflict_level} | dom={dominant_pct:.0f}% | "
        f"score={conflict_score:.0f} | pairs={len(specific_conflicts)} | "
        f"trade={'✅' if trade_allowed else '❌'}"
    )

    return {
        "conflict":           is_conflict,
        "conflict_level":     conflict_level,
        "trade_allowed":      trade_allowed,
        "conflict_score":     round(conflict_score, 1),
        "trade_size_factor":  trade_size_factor,
        "specific_conflicts": specific_conflicts,
        "dominant_pct":       round(dominant_pct, 1),
        "pair_count":         len(specific_conflicts),
        "resolution_hint":    resolution_hint,
    }


def _detect_pair_conflicts(
    engine_results: Dict[str, EngineResult],
) -> List[dict]:
    """
    Verifica pares de engines críticos para contradições diretas.

    Um par é conflitante se um engine é BULLISH e o outro é BEARISH.
    """
    conflicts: List[dict] = []

    for eng_a, eng_b, severity, desc in CRITICAL_PAIRS:
        res_a = engine_results.get(eng_a)
        res_b = engine_results.get(eng_b)

        if not res_a or not res_b:
            continue
        if not res_a.valid or not res_b.valid:
            continue
        if res_a.direction == "NEUTRAL" or res_b.direction == "NEUTRAL":
            continue

        if res_a.direction != res_b.direction:
            # Conflito detectado
            # Intensidade: média das confianças dos dois engines
            avg_conf = (res_a.confidence + res_b.confidence) / 2.0
            conflicts.append({
                "pair":        f"{eng_a} vs {eng_b}",
                "engine_a":    eng_a,
                "engine_b":    eng_b,
                "dir_a":       res_a.direction,
                "dir_b":       res_b.direction,
                "severity":    severity,
                "description": desc,
                "avg_confidence": round(avg_conf, 1),
            })

    return conflicts


def _build_resolution_hint(
    level: str,
    dominant_pct: float,
    specific_conflicts: List[dict],
    consensus: str,
) -> str:
    """Gera texto descritivo para resolução do conflito."""
    if level == "LOW":
        return f"Consenso sólido ({dominant_pct:.0f}% {consensus}). Prosseguir com tamanho normal."

    if level == "HIGH":
        pair_list = ", ".join(c["pair"] for c in specific_conflicts[:2])
        base = f"Conflito severo ({dominant_pct:.0f}% dominante). Sem trade recomendado."
        if pair_list:
            base += f" Pares críticos: {pair_list}."
        return base

    # MEDIUM
    pairs = [c["pair"] for c in specific_conflicts if c["severity"] == "HIGH"]
    if pairs:
        return (
            f"Conflito moderado ({dominant_pct:.0f}% {consensus}). "
            f"Reduzir posição 35%. Atenção: {', '.join(pairs)}."
        )
    return (
        f"Conflito moderado ({dominant_pct:.0f}% {consensus}). "
        f"Reduzir posição 35%. Aguardar confirmação adicional."
    )
