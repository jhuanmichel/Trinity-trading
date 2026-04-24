"""
OutcomeSampler — controla quais candidatos tem outcome registrado.

Problema: registrar TODOS candidatos com score >= 35 estoura disco
(~30k outcomes/dia). Perda de estatistica significativa so nos scores
muito baixos — scores proximos do threshold de alerta sao os mais
importantes pra calibracao ML.

Solucao: sample rate estratificado.
  score >= 80  → 100% registrado (zona de alerta Telegram, todos criticos)
  60 <= score < 80 → 30%  (zona proxima do limiar, informativa pra ML)
  35 <= score < 60 → 10%  (zona baixa, util mas volumosa)
  score < 35 → 0%  (irrelevante)

Sampling DETERMINISTICO via hash do signal_id: mesmo sinal sempre cai
no mesmo lado, permitindo replay de analise.
"""

import hashlib
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# Config — ajustavel por env var no futuro
THRESHOLDS = [
    (80, 1.00),   # score >= 80 → 100%
    (60, 0.30),   # 60-79 → 30%
    (35, 0.10),   # 35-59 → 10%
]
MIN_SCORE_FOR_ANY_REGISTRATION = 35


def should_register(score: float, signal_id: str = "") -> Tuple[bool, str]:
    """
    Decide se um candidato deve ter outcome registrado.

    Args:
        score: score final do candidato (0-100+ com boosts)
        signal_id: id unico do sinal (pra sampling deterministico).
                   Se vazio, usa hash de score (aceitavel mas nao ideal).

    Returns:
        (should, bucket_label):
            should=True/False
            bucket_label: 'alert' | 'near' | 'low' | 'skip' | '..._sampled_out'
    """
    try:
        score = float(score)
    except (TypeError, ValueError):
        return False, "skip"

    if score < MIN_SCORE_FOR_ANY_REGISTRATION:
        return False, "skip"

    for threshold, rate in THRESHOLDS:
        if score >= threshold:
            if rate >= 1.0:
                return True, _label(threshold)
            # Sampling deterministico via hash
            key = signal_id or f"{score:.4f}"
            h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
            normalized = (h % 10000) / 10000.0  # [0.0, 1.0)
            if normalized < rate:
                return True, _label(threshold)
            return False, f"{_label(threshold)}_sampled_out"

    return False, "skip"


def _label(threshold: int) -> str:
    if threshold >= 80:
        return "alert"
    elif threshold >= 60:
        return "near"
    elif threshold >= 35:
        return "low"
    return "skip"


def stats_for_dashboard() -> dict:
    """Config expostos via /api/outcome-sampler/stats."""
    return {
        "min_score": MIN_SCORE_FOR_ANY_REGISTRATION,
        "buckets": [
            {"min_score": t, "rate": r, "label": _label(t)}
            for t, r in THRESHOLDS
        ],
    }
