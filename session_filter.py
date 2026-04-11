"""
session_filter.py — Filtro de sessão institucional para o Altcoin Scanner

Janelas válidas (UTC):
  - London Open:    07–10
  - NY Open:        12–16
  - Asia/London:    19–22

Sinais fora dessas janelas são bloqueados, EXCETO se score >= SESSION_BYPASS_SCORE (75).
Isso evita ruído em períodos de baixa liquidez (Asia tarde, dead hours, etc.).
"""
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# ── Configuração ──────────────────────────────────────────────────────────────
VALID_SESSIONS_UTC: list[tuple[int, int]] = [(7, 10), (12, 16), (19, 22)]
SESSION_BYPASS_SCORE: float = 75.0

SESSION_NAMES: dict[tuple[int, int], str] = {
    (7,  10): "London Open",
    (12, 16): "NY Open",
    (19, 22): "Asia/London",
}


# ── Funções públicas ──────────────────────────────────────────────────────────

def is_valid_session(dt: Optional[datetime] = None) -> bool:
    """
    Retorna True se o horário está dentro de uma janela institucional válida.

    >>> from datetime import datetime, timezone
    >>> is_valid_session(datetime(2026, 4, 11, 8, 30, tzinfo=timezone.utc))
    True
    >>> is_valid_session(datetime(2026, 4, 11, 11, 0, tzinfo=timezone.utc))
    False
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    hour = dt.hour
    return any(start <= hour < end for start, end in VALID_SESSIONS_UTC)


def should_bypass_session(score: float) -> bool:
    """Score >= SESSION_BYPASS_SCORE contorna o filtro de sessão."""
    return score >= SESSION_BYPASS_SCORE


def _current_session_name(dt: Optional[datetime] = None) -> str:
    """Retorna o nome da sessão ativa, ou string vazia se fora de sessão."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    hour = dt.hour
    for start, end in VALID_SESSIONS_UTC:
        if start <= hour < end:
            return SESSION_NAMES.get((start, end), f"{start}–{end}h UTC")
    return ""


def _next_session_label(dt: Optional[datetime] = None) -> str:
    """Retorna nome + hora UTC da próxima sessão (para exibir no dashboard)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    hour = dt.hour
    for start, end in VALID_SESSIONS_UTC:
        if hour < start:
            name = SESSION_NAMES.get((start, end), f"{start}h UTC")
            return f"{name} {start}h UTC"
    # passou da última sessão do dia → primeira sessão de amanhã
    first_start, first_end = VALID_SESSIONS_UTC[0]
    name = SESSION_NAMES.get((first_start, first_end), f"{first_start}h UTC")
    return f"{name} {first_start}h UTC (amanhã)"


def apply_session_filter(
    direction: str,
    score: float,
    dt: Optional[datetime] = None,
) -> dict:
    """
    Aplica o filtro de sessão a um sinal. Retorna dict:

      allowed       (bool)  — True se o sinal pode prosseguir
      bypassed      (bool)  — True se aprovado pelo score alto (fora de sessão)
      session_name  (str)   — nome da sessão ativa, vazio se fora
      next_session  (str)   — label da próxima sessão
      reason        (str)   — "in_session" | "bypass_high_score" | "out_of_session" | "neutro"

    >>> from datetime import datetime, timezone
    >>> apply_session_filter("LONG", 80, datetime(2026, 4, 11, 11, 0, tzinfo=timezone.utc))["allowed"]
    True
    >>> apply_session_filter("LONG", 50, datetime(2026, 4, 11, 11, 0, tzinfo=timezone.utc))["allowed"]
    False
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    # Sinais neutros/bloqueados não precisam de filtro de sessão
    if direction in ("NEUTRO", "NO_TRADE"):
        return {
            "allowed":      True,
            "bypassed":     False,
            "session_name": _current_session_name(dt),
            "next_session": _next_session_label(dt),
            "reason":       "neutro",
        }

    valid  = is_valid_session(dt)
    bypass = should_bypass_session(score)

    if valid or bypass:
        reason = "bypass_high_score" if (bypass and not valid) else "in_session"
        log.debug(
            f"[session_filter] {direction} score={score:.1f} → PERMITIDO "
            f"({'bypass score≥75' if reason == 'bypass_high_score' else 'em sessão'})"
        )
        return {
            "allowed":      True,
            "bypassed":     bypass and not valid,
            "session_name": _current_session_name(dt),
            "next_session": _next_session_label(dt),
            "reason":       reason,
        }

    log.debug(
        f"[session_filter] {direction} score={score:.1f} → BLOQUEADO "
        f"(fora de sessão, score < {SESSION_BYPASS_SCORE})"
    )
    return {
        "allowed":      False,
        "bypassed":     False,
        "session_name": "",
        "next_session": _next_session_label(dt),
        "reason":       "out_of_session",
    }
