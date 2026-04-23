"""
Boost Manager — centraliza aplicacao de multiplicadores de score.

Contexto (22/04/2026):
- pump_scoring_engine / crash_scoring_engine aplicam boosts encadeados
  (momentum x overext x funding_extreme x BTC x DNA), alguns ate 1.70x.
- Scores inflados artificialmente viram tier HIGH/STRONG, mas WR empirica
  mostra tier HIGH performando PIOR que tier MICRO em LONG.
- Fase 1 confirmou inversao (PHASE_1_STATS_CLEAN.md: LONG HIGH 29%, LONG MICRO 38%).

Design V2:
- Cap individual: 1.30x por boost
- Cap total combinado: 1.80x independente de quantos boosts
- Audit trail: cada boost registrado com valor + razao no outcome.

Uso:
    bundle = ScoreBundle(base=50)
    bundle.add_boost('momentum', 1.30, reason='pct_change_24h=120%')
    bundle.add_boost('btc_align', 1.15, reason='btc_regime_bull')
    final = bundle.final_score()       # 50 * min(1.30*1.15, 1.80) = ~75
    audit = bundle.audit()             # dict serializavel p/ outcome
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

# Cap matematico absoluto. Alterar exige analise estatistica +
# regression test (scripts/scoring_regression_test.py).
BOOST_CAP_INDIVIDUAL: float = 1.30
BOOST_CAP_TOTAL: float      = 1.80


@dataclass
class BoostEntry:
    """Um boost aplicado ao score. Imutavel apos add."""
    name:   str
    value:  float
    reason: str = ""


@dataclass
class ScoreBundle:
    """
    Bundle em construcao. Boosts adicionados via add_boost() com cap
    individual. final_score() aplica cap total sobre multiplicacao.
    """
    base:   float
    boosts: List[BoostEntry] = field(default_factory=list)

    def add_boost(self, name: str, value: float, reason: str = "") -> None:
        """
        Adiciona boost. Convencoes:
          - value < 1.0 → convertido para 1.0 (boosts nao penalizam; ajuste base direto).
          - value > BOOST_CAP_INDIVIDUAL → capped + info log.
        """
        if value < 1.0:
            logger.warning(
                "[BoostManager] boost %s=%.3f < 1.0 — convertendo para 1.0 "
                "(use score base para penalizar)",
                name, value,
            )
            value = 1.0
        if value > BOOST_CAP_INDIVIDUAL:
            logger.info(
                "[BoostManager] boost %s=%.3f capped to %.2f",
                name, value, BOOST_CAP_INDIVIDUAL,
            )
            value = BOOST_CAP_INDIVIDUAL
        self.boosts.append(BoostEntry(name=name, value=value, reason=reason))

    def total_multiplier(self) -> float:
        """Multiplicador combinado antes do cap total."""
        if not self.boosts:
            return 1.0
        total = 1.0
        for b in self.boosts:
            total *= b.value
        return total

    def effective_multiplier(self) -> float:
        """Multiplicador combinado apos cap total."""
        return min(self.total_multiplier(), BOOST_CAP_TOTAL)

    def final_score(self) -> float:
        """Score final = base * effective_multiplier."""
        return self.base * self.effective_multiplier()

    def audit(self) -> dict:
        """Estrutura serializavel para persistencia no outcome."""
        total = self.total_multiplier()
        eff   = self.effective_multiplier()
        return {
            "base":                 round(self.base, 2),
            "boosts": [
                {
                    "name":   b.name,
                    "value":  round(b.value, 3),
                    "reason": b.reason,
                }
                for b in self.boosts
            ],
            "total_multiplier":     round(total, 3),
            "effective_multiplier": round(eff, 3),
            "capped":               total > BOOST_CAP_TOTAL,
            "final":                round(self.final_score(), 2),
        }
