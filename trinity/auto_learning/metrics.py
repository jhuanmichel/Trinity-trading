"""
Metricas calculadas a partir dos outcomes.
Usadas por todos os modulos auto-learning pra tomar decisoes.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("auto_learning.metrics")


def get_outcomes_dir() -> Path:
    """Diretorio de outcomes (persistent disk preferido)."""
    if Path("/data/logs").exists():
        return Path("/data/logs")
    return Path("logs")


# Cache simples (TTL 60s) pra evitar releitura constante
_cache = {"outcomes": None, "loaded_at": None, "days_loaded": 0}
_CACHE_TTL_SECONDS = 60


def _load_recent_outcomes(days: int = 30, force_reload: bool = False) -> list[dict]:
    """
    Carrega outcomes dos ultimos N dias.
    Usa cache de 60s pra performance.
    """
    now = datetime.now(timezone.utc)

    # Cache hit?
    if (
        not force_reload
        and _cache["outcomes"] is not None
        and _cache["loaded_at"] is not None
        and (now - _cache["loaded_at"]).total_seconds() < _CACHE_TTL_SECONDS
        and _cache["days_loaded"] >= days
    ):
        # Filtrar pelo periodo pedido
        cutoff = now - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()
        return [
            o for o in _cache["outcomes"]
            if (o.get("timestamp") or o.get("registered_at") or "") >= cutoff_iso
        ]

    # Reload
    outcomes_dir = get_outcomes_dir()
    cutoff = now - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()

    outcomes = []

    try:
        for f in sorted(outcomes_dir.glob("outcomes_*.jsonl")):
            try:
                with open(f) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            o = json.loads(line)
                            ts = o.get("timestamp") or o.get("registered_at") or ""
                            if ts and ts >= cutoff_iso:
                                outcomes.append(o)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning(f"[METRICS] Erro lendo {f}: {e}")
    except Exception as e:
        logger.error(f"[METRICS] Erro listando outcomes: {e}")
        return []

    # Ordenar por timestamp
    outcomes.sort(key=lambda o: o.get("timestamp") or o.get("registered_at") or "")

    # Atualizar cache
    _cache["outcomes"] = outcomes
    _cache["loaded_at"] = now
    _cache["days_loaded"] = days

    return outcomes


def _is_resolved(outcome: dict) -> Optional[bool]:
    """
    Retorna:
        True  se WIN
        False se LOSS
        None  se nao resolvido (NEUTRAL/PENDING/EXPIRED)
    """
    status = str(outcome.get("status", "")).upper()
    if status == "WIN" or "TP" in status:
        return True
    if status == "LOSS" or "STOP" in status:
        return False
    return None


def _get_score(outcome: dict) -> float:
    """Extrai score do outcome (suporta multiplos nomes de campo)."""
    for key in ("score", "opportunity_score", "final_score"):
        val = outcome.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return 0.0


def _get_ts(outcome: dict) -> str:
    """Pega timestamp do outcome (timestamp ou registered_at)."""
    return outcome.get("timestamp") or outcome.get("registered_at") or ""


# ============================================================
# WR by various dimensions
# ============================================================

def get_recent_wr_by_day(days: int = 7) -> list[dict]:
    """
    WR diario dos ultimos N dias.

    Returns:
        list[{"date": "2026-04-18", "wr": 0.45, "trades": 120, "wins": 54, "losses": 66}]
        Ordenado do mais antigo pro mais recente.
    """
    outcomes = _load_recent_outcomes(days=days + 2)  # margem por timezone

    by_day = defaultdict(lambda: {"wins": 0, "losses": 0})

    for o in outcomes:
        ts = _get_ts(o)
        if not ts:
            continue
        try:
            day = ts[:10]  # YYYY-MM-DD
        except (TypeError, IndexError):
            continue

        resolved = _is_resolved(o)
        if resolved is True:
            by_day[day]["wins"] += 1
        elif resolved is False:
            by_day[day]["losses"] += 1

    result = []
    for day in sorted(by_day.keys())[-days:]:
        d = by_day[day]
        total = d["wins"] + d["losses"]
        result.append({
            "date": day,
            "wr": d["wins"] / total if total > 0 else 0.0,
            "trades": total,
            "wins": d["wins"],
            "losses": d["losses"],
        })

    return result


def get_overall_wr(days: int = 30) -> dict:
    """WR global dos ultimos N dias."""
    outcomes = _load_recent_outcomes(days=days)

    wins = sum(1 for o in outcomes if _is_resolved(o) is True)
    losses = sum(1 for o in outcomes if _is_resolved(o) is False)
    total = wins + losses

    return {
        "wins": wins,
        "losses": losses,
        "total_resolved": total,
        "wr": wins / total if total > 0 else 0.0,
        "days": days,
    }


def get_wr_by_source(days: int = 30, min_trades: int = 50) -> dict[str, dict]:
    """
    WR por source (pump_trader, crash_trader, market_movers_scanner, etc).
    Filtra sources com >= min_trades.
    """
    outcomes = _load_recent_outcomes(days=days)
    by_source = defaultdict(lambda: {"wins": 0, "losses": 0})

    for o in outcomes:
        source = o.get("source", "unknown")
        resolved = _is_resolved(o)
        if resolved is True:
            by_source[source]["wins"] += 1
        elif resolved is False:
            by_source[source]["losses"] += 1

    result = {}
    for source, stats in by_source.items():
        total = stats["wins"] + stats["losses"]
        if total >= min_trades:
            result[source] = {
                "wr": stats["wins"] / total,
                "trades": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
            }
    return result


def get_wr_by_symbol(days: int = 30, min_trades: int = 10) -> dict[str, dict]:
    """WR por simbolo. Filtra com >= min_trades."""
    outcomes = _load_recent_outcomes(days=days)
    by_symbol = defaultdict(lambda: {"wins": 0, "losses": 0})

    for o in outcomes:
        symbol = o.get("symbol", "")
        if not symbol:
            continue
        resolved = _is_resolved(o)
        if resolved is True:
            by_symbol[symbol]["wins"] += 1
        elif resolved is False:
            by_symbol[symbol]["losses"] += 1

    result = {}
    for symbol, stats in by_symbol.items():
        total = stats["wins"] + stats["losses"]
        if total >= min_trades:
            result[symbol] = {
                "wr": stats["wins"] / total,
                "trades": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
            }
    return result


def get_wr_by_direction_regime(
    days: int = 30,
    min_trades: int = 50,
) -> dict[tuple[str, str], dict]:
    """
    WR cruzando direction x btc_regime.
    Key: (direction, regime), valor: stats.
    """
    outcomes = _load_recent_outcomes(days=days)
    by_combo = defaultdict(lambda: {"wins": 0, "losses": 0})

    for o in outcomes:
        direction = str(o.get("direction", "")).upper()
        regime = o.get("btc_regime", "UNKNOWN")

        if direction not in ("LONG", "SHORT"):
            continue

        key = (direction, regime)
        resolved = _is_resolved(o)
        if resolved is True:
            by_combo[key]["wins"] += 1
        elif resolved is False:
            by_combo[key]["losses"] += 1

    result = {}
    for key, stats in by_combo.items():
        total = stats["wins"] + stats["losses"]
        if total >= min_trades:
            result[key] = {
                "wr": stats["wins"] / total,
                "trades": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
            }
    return result


def get_wr_by_score_band(
    days: int = 30,
    min_trades: int = 30,
) -> dict[tuple[int, int], dict]:
    """WR por faixa de score."""
    outcomes = _load_recent_outcomes(days=days)

    bands = [(0, 35), (35, 45), (45, 55), (55, 65), (65, 75), (75, 90), (90, 101)]
    by_band = {b: {"wins": 0, "losses": 0} for b in bands}

    for o in outcomes:
        score = _get_score(o)
        for (lo, hi) in bands:
            if lo <= score < hi:
                resolved = _is_resolved(o)
                if resolved is True:
                    by_band[(lo, hi)]["wins"] += 1
                elif resolved is False:
                    by_band[(lo, hi)]["losses"] += 1
                break

    result = {}
    for band, stats in by_band.items():
        total = stats["wins"] + stats["losses"]
        if total >= min_trades:
            result[band] = {
                "wr": stats["wins"] / total,
                "trades": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
            }
    return result


def get_volume_stats(days: int = 30) -> dict:
    """
    Estatisticas de volume (nao de WR).
    """
    outcomes = _load_recent_outcomes(days=days)

    total = len(outcomes)
    resolved = sum(1 for o in outcomes if _is_resolved(o) is not None)
    pending = total - resolved

    # Por dia
    by_day = defaultdict(int)
    for o in outcomes:
        ts = _get_ts(o)
        if ts:
            day = ts[:10]
            by_day[day] += 1

    avg_per_day = sum(by_day.values()) / len(by_day) if by_day else 0

    return {
        "total": total,
        "resolved": resolved,
        "pending": pending,
        "days": days,
        "avg_per_day": avg_per_day,
        "days_with_data": len(by_day),
    }


def get_days_of_data() -> int:
    """Retorna quantos dias de dados existem (do mais antigo ate hoje)."""
    outcomes_dir = get_outcomes_dir()
    files = sorted(outcomes_dir.glob("outcomes_*.jsonl"))
    if not files:
        return 0

    earliest = None
    for f in files:
        try:
            with open(f) as fh:
                first_line = fh.readline().strip()
                if first_line:
                    o = json.loads(first_line)
                    ts = _get_ts(o)
                    if ts:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if earliest is None or dt < earliest:
                            earliest = dt
        except Exception:
            continue

    if earliest is None:
        return 0

    delta = datetime.now(timezone.utc) - earliest
    return delta.days
