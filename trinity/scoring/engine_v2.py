"""
Scoring Engine V2 — wrappers que aplicam BoostManager aos componentes
calculados por score_pump / score_crash (V1).

Estrategia pragmatica: V1 continua calculando seus 4x25 componentes +
detectando condicoes de boost (pct_change_24h, funding_rate, ls_ratio,
dna_pattern, btc_regime). V2 pega os **mesmos componentes base** + as
**mesmas condicoes** e aplica via ScoreBundle (com caps).

Diferenca observavel:
- V1: score_base × momentum_mult × overext_mult × funding_mult × ...
  (cumulativo ilimitado, cada `min(100, ...)` aplicado one-shot, total
  pode inflar muito antes do cap)
- V2: ScoreBundle.add_boost() individual cap 1.30 + total cap 1.80, audit.

API:
- score_pump_v2(base_components, coin_data, funding_result=None)
    → (final_score, audit_dict)
- score_crash_v2(base_components, coin_data, funding_result=None)
    → (final_score, audit_dict)

`base_components`: dict com keys 'silent', 'squeeze', 'gravity',
'breakout' (pump) ou 'collapse', 'whale', 'vol', 'cascade' (crash),
cada um um float 0-25.

`coin_data`: dict com pct_change_24h, ls_ratio, funding_rate, btc_bias,
dna_pattern — campos usados para derivar boost values.

`funding_result`: optional output de analyze_funding_extreme()
com field `composite_mult` (1.0-2.50). Se presente, vira boost.
"""
from __future__ import annotations

import logging
from typing import Optional

from trinity.scoring.boost_manager import ScoreBundle

logger = logging.getLogger(__name__)


def _momentum_mult(coin_data: dict) -> float:
    """Replica logica de momentum_mult do pump_scoring_engine (linhas ~200-220)."""
    pct     = float(coin_data.get("pct_change_24h", coin_data.get("riseFallRate", 0)) or 0)
    funding = float(coin_data.get("funding_rate", 0) or 0)
    fund_ann = funding * 3.0 * 365.0 * 100.0

    # Squeeze ativo (funding negativo + pump forte) = boost maximo no V1
    if pct > 10 and fund_ann < -20:
        return 1.40  # era o "boost máximo" do V1, V2 capará em 1.30
    if pct > 8:
        return 1.30
    if pct > 5:
        return 1.20
    if pct > 3:
        return 1.10
    return 1.0


def _overext_mult(coin_data: dict) -> float:
    """Replica tabela overext_mult do crash_scoring_engine (linhas 186-192)."""
    pct = float(coin_data.get("pct_change_24h", coin_data.get("riseFallRate", 0)) or 0)
    if pct > 150: return 1.70
    if pct > 100: return 1.50
    if pct > 70:  return 1.35
    if pct > 50:  return 1.25
    if pct > 30:  return 1.15
    if pct > 20:  return 1.08
    return 1.0


def _lev_amp(coin_data: dict) -> float:
    """Replica lev_amp do crash_scoring_engine (linha 157)."""
    ls_ratio = float(coin_data.get("ls_ratio", 1.0) or 1.0)
    return 1.0 + max(0.0, (ls_ratio - 1.5) * 0.25)


def _dna_boost(coin_data: dict) -> float:
    """DNA pattern boost (conservador)."""
    if coin_data.get("dna_pattern") or coin_data.get("pump_dna"):
        return 1.10
    return 1.0


def _btc_boost(coin_data: dict, direction: str = "LONG") -> float:
    """BTC regime alignment boost."""
    bias = str(coin_data.get("btc_bias") or coin_data.get("btc_regime") or "").upper()
    if direction == "LONG" and "BULL" in bias:
        return 1.10
    if direction == "SHORT" and "BEAR" in bias:
        return 1.10
    return 1.0


def score_pump_v2(
    base_components: dict,
    coin_data: Optional[dict] = None,
    funding_result: Optional[dict] = None,
) -> tuple[float, dict]:
    """
    V2 pump scoring via BoostManager.

    Args:
      base_components: {'silent':0-25, 'squeeze':0-25, 'gravity':0-25, 'breakout':0-25}
      coin_data: dict com campos do ticker (pct_change_24h, funding_rate, ls_ratio,
                 btc_bias, dna_pattern)
      funding_result: optional output de analyze_funding_extreme() com composite_mult

    Returns: (final_score, audit_dict)
    """
    cd = coin_data or {}
    base = sum(float(v or 0) for v in base_components.values())
    bundle = ScoreBundle(base=base)

    # Momentum
    m = _momentum_mult(cd)
    if m > 1.0:
        bundle.add_boost("momentum", m, reason=f"pct={cd.get('pct_change_24h','?')}%")

    # Funding extreme (maior multiplicador do V1, capped pelo BoostManager)
    if funding_result and funding_result.get("composite_mult", 1.0) > 1.0:
        fm = float(funding_result["composite_mult"])
        bundle.add_boost(
            "funding_extreme", fm,
            reason=f"tier={funding_result.get('tier','?')}",
        )

    # DNA pattern
    dna = _dna_boost(cd)
    if dna > 1.0:
        bundle.add_boost("dna_pattern", dna, reason="pump_dna_match")

    # BTC alignment
    btc = _btc_boost(cd, direction="LONG")
    if btc > 1.0:
        bundle.add_boost("btc_align", btc, reason=f"btc_bias={cd.get('btc_bias','?')}")

    return bundle.final_score(), bundle.audit()


def score_crash_v2(
    base_components: dict,
    coin_data: Optional[dict] = None,
    funding_result: Optional[dict] = None,
) -> tuple[float, dict]:
    """
    V2 crash scoring via BoostManager.

    Args:
      base_components: {'collapse':0-25, 'whale':0-25, 'vol':0-25, 'cascade':0-25}
      coin_data, funding_result: mesmo esquema do pump.

    Returns: (final_score, audit_dict)
    """
    cd = coin_data or {}
    base = sum(float(v or 0) for v in base_components.values())
    bundle = ScoreBundle(base=base)

    # Overextended (tabela crash_scoring:186-192)
    oe = _overext_mult(cd)
    if oe > 1.0:
        bundle.add_boost("overextended", oe, reason=f"pct={cd.get('pct_change_24h','?')}%")

    # Leverage amplification
    la = _lev_amp(cd)
    if la > 1.0:
        bundle.add_boost("leverage_amp", la, reason=f"ls_ratio={cd.get('ls_ratio','?')}")

    # Funding extreme
    if funding_result and funding_result.get("composite_mult", 1.0) > 1.0:
        fm = float(funding_result["composite_mult"])
        bundle.add_boost(
            "funding_extreme", fm,
            reason=f"tier={funding_result.get('tier','?')}",
        )

    # DNA + BTC
    dna = _dna_boost(cd)
    if dna > 1.0:
        bundle.add_boost("dna_pattern", dna, reason="crash_dna_match")

    btc = _btc_boost(cd, direction="SHORT")
    if btc > 1.0:
        bundle.add_boost("btc_align", btc, reason=f"btc_bias={cd.get('btc_bias','?')}")

    return bundle.final_score(), bundle.audit()
