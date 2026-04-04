"""
halving_model.py — Modelo de Halvings do Bitcoin
Trinity Trading | Bitcoin Cycle Engine

Halvings históricos:
  2012-11-28: 50 → 25 BTC/bloco
  2016-07-09: 25 → 12.5 BTC/bloco
  2020-05-11: 12.5 → 6.25 BTC/bloco
  2024-04-19: 6.25 → 3.125 BTC/bloco  (último)
  2028 (est.): 3.125 → 1.5625 BTC/bloco (próximo)

Janelas de fase por dias desde último halving:
  ACCUMULATION:  0 – 180 dias
  EARLY:       180 – 400 dias
  MID:         400 – 700 dias
  LATE:        700 – 900 dias
  BEAR:        900 – 1440 dias
"""

from datetime import datetime, timezone

# ─── Halvings e próximo estimado ─────────────────────────────────────────────

HALVING_DATES = [
    datetime(2012, 11, 28, tzinfo=timezone.utc),
    datetime(2016,  7,  9, tzinfo=timezone.utc),
    datetime(2020,  5, 11, tzinfo=timezone.utc),
    datetime(2024,  4, 19, tzinfo=timezone.utc),  # último halving
]

NEXT_HALVING  = datetime(2028, 4, 15, tzinfo=timezone.utc)   # estimativa
CURRENT_REWARD = 3.125   # BTC/bloco após halving de 2024

# Janelas de fase (dias desde último halving)
PHASE_WINDOWS = {
    "ACCUMULATION": (0,   180),
    "EARLY":        (180, 400),
    "MID":          (400, 700),
    "LATE":         (700, 900),
    "BEAR":         (900, 1440),
}

# Score de favorabilidade de ciclo por fase (0-100)
PHASE_SCORES = {
    "ACCUMULATION": 65.0,   # oportunidade de acumulação
    "EARLY":        82.0,   # melhor ponto de entrada bull
    "MID":          72.0,   # ainda forte, momentum
    "LATE":         38.0,   # risco crescente, reduzir exposição
    "BEAR":         22.0,   # downtrend esperado
}


# ─── Run ─────────────────────────────────────────────────────────────────────

def run_halving_model() -> dict:
    """
    Calcula a posição do ciclo de halving do Bitcoin.

    Returns:
        {
            "halving_phase":       "ACCUMULATION" | "EARLY" | "MID" | "LATE" | "BEAR",
            "halving_score":       float 0-100,
            "days_since_halving":  int,
            "days_to_next":        int,
            "cycle_age_pct":       float 0-100,
            "last_halving_date":   str (ISO),
            "next_halving_date":   str (ISO),
            "reward_btc":          float,
        }
    """
    now          = datetime.now(timezone.utc)
    last_halving = HALVING_DATES[-1]

    days_since    = (now - last_halving).days
    days_to_next  = max(0, (NEXT_HALVING - now).days)
    cycle_length  = (NEXT_HALVING - last_halving).days          # ~1457 dias
    cycle_age_pct = round(min(100.0, (days_since / cycle_length) * 100), 1)

    # Determina fase
    halving_phase = "BEAR"   # padrão se além de todas as janelas
    for phase, (start, end) in PHASE_WINDOWS.items():
        if start <= days_since < end:
            halving_phase = phase
            break

    halving_score = PHASE_SCORES.get(halving_phase, 25.0)

    return {
        "halving_phase":      halving_phase,
        "halving_score":      halving_score,
        "days_since_halving": days_since,
        "days_to_next":       days_to_next,
        "cycle_age_pct":      cycle_age_pct,
        "last_halving_date":  last_halving.date().isoformat(),
        "next_halving_date":  NEXT_HALVING.date().isoformat(),
        "reward_btc":         CURRENT_REWARD,
    }
