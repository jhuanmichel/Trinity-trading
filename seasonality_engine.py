"""
seasonality_engine.py — Motor de sazonalidade para o Trinity Bot.

Aplica multiplicadores de confiança (0.75–1.25) ao score dos sinais
com base em padrões históricos reais de performance do BTC por
dia da semana e hora UTC.

Dados gerados por: scripts/calculate_seasonality.py
Arquivo lido em memória: dashboard/seasonality_data.json

Funciona com multiplicadores neutros (1.0) se o arquivo não existir —
o bot nunca depende da existência dos dados sazonais.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
SEASONALITY_FILE    = Path("dashboard/seasonality_data.json")
CONFIDENCE_REQUIRED = "medium"   # ignora multiplicadores com confidence "low"
NEUTRAL_MULTIPLIER  = 1.0
FLOOR               = 0.75
CEILING             = 1.25

# Ordem para comparação de confiança
_CONF_RANK = {"high": 2, "medium": 1, "low": 0}

# Nomes dos dias em português
_DAY_NAMES = [
    "Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira",
    "Sexta-feira", "Sábado", "Domingo",
]

# ── Singleton ─────────────────────────────────────────────────────────────────
_instance: Optional["SeasonalityEngine"] = None


def get_seasonality_engine() -> "SeasonalityEngine":
    """Retorna instância singleton do SeasonalityEngine."""
    global _instance
    if _instance is None:
        _instance = SeasonalityEngine()
    return _instance


class SeasonalityEngine:
    """
    Motor de sazonalidade — aplica multiplicadores baseados em histórico BTC.

    Uso:
        engine = get_seasonality_engine()
        result = engine.apply_to_score(score=72.5)
        score_para_threshold = result["score_adjusted"]
    """

    def __init__(self) -> None:
        self._data: Optional[dict] = None
        self._load()

    # ── Carregamento ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        """
        Carrega seasonality_data.json em memória.
        Nunca lança exceção — multipliers neutros como fallback.
        """
        try:
            if SEASONALITY_FILE.exists():
                raw = json.loads(SEASONALITY_FILE.read_text())
                if "by_day_of_week" in raw and "by_hour_utc" in raw:
                    self._data = raw
                    logger.info(
                        "[SeasonalityEngine] Dados carregados: "
                        f"{raw.get('total_candles_analyzed', '?')} candles | "
                        f"gerado em {raw.get('generated_at', '?')}"
                    )
                else:
                    logger.warning(
                        "[SeasonalityEngine] Arquivo sem campos obrigatórios — "
                        "usando multiplicadores neutros"
                    )
                    self._data = None
            else:
                logger.warning(
                    f"[SeasonalityEngine] {SEASONALITY_FILE} não encontrado. "
                    "Multiplicadores neutros (1.0) ativos. "
                    "Execute: python scripts/calculate_seasonality.py"
                )
                self._data = None
        except Exception as e:
            logger.warning(f"[SeasonalityEngine] Erro ao carregar: {e} — usando neutros")
            self._data = None

    def reload(self) -> None:
        """Recarrega o arquivo do disco. Chamado após recalculo mensal."""
        self._load()

    # ── Interface pública ─────────────────────────────────────────────────────

    def get_multiplier(self, dt: Optional[datetime] = None) -> dict:
        """
        Retorna multiplicadores para o datetime fornecido.
        Se dt=None, usa datetime.now(timezone.utc).

        Retorna:
            {
              "day_multiplier":      float,
              "hour_multiplier":     float,
              "combined_multiplier": float,   # day × hour, capped [0.75, 1.25]
              "day_label":           str,
              "hour_label":          str,
              "context":             str,
              "data_available":      bool,
            }
        """
        if dt is None:
            dt = datetime.now(timezone.utc)

        dow  = dt.weekday()   # 0=segunda, 6=domingo
        hour = dt.hour

        if not self._data:
            return {
                "day_multiplier":      NEUTRAL_MULTIPLIER,
                "hour_multiplier":     NEUTRAL_MULTIPLIER,
                "combined_multiplier": NEUTRAL_MULTIPLIER,
                "day_label":           _DAY_NAMES[dow],
                "hour_label":          f"{hour}h UTC",
                "context":             "Neutro — dados sazonais não disponíveis",
                "data_available":      False,
            }

        by_day  = self._data.get("by_day_of_week", {})
        by_hour = self._data.get("by_hour_utc", {})

        day_entry  = by_day.get(str(dow), {})
        hour_entry = by_hour.get(str(hour), {})

        # Confidence gate: se confiança do dia < CONFIDENCE_REQUIRED → neutro
        day_conf = day_entry.get("confidence", "low")
        day_mult = (
            day_entry.get("multiplier", NEUTRAL_MULTIPLIER)
            if _CONF_RANK.get(day_conf, 0) >= _CONF_RANK[CONFIDENCE_REQUIRED]
            else NEUTRAL_MULTIPLIER
        )

        hour_mult = hour_entry.get("multiplier", NEUTRAL_MULTIPLIER)

        combined = round(max(FLOOR, min(CEILING, day_mult * hour_mult)), 4)

        return {
            "day_multiplier":      round(day_mult, 4),
            "hour_multiplier":     round(hour_mult, 4),
            "combined_multiplier": combined,
            "day_label":           day_entry.get("label", _DAY_NAMES[dow]),
            "hour_label":          f"{hour}h UTC",
            "context":             self._generate_context_label(day_mult, hour_mult),
            "data_available":      True,
        }

    def apply_to_score(self, score: float, dt: Optional[datetime] = None) -> dict:
        """
        Aplica multiplicador sazonal ao score e retorna resultado completo.
        O score original NUNCA é modificado — apenas score_adjusted é novo.

        Retorna:
            {
              "score_original": float,
              "score_adjusted": float,
              "multiplier":     dict,    # retorno completo de get_multiplier()
              "adjustment_pct": float,   # ex: +18.0 ou -15.0
            }
        """
        multiplier = self.get_multiplier(dt)
        combined   = multiplier["combined_multiplier"]

        if not multiplier["data_available"]:
            # Sem dados: score original sem ajuste
            return {
                "score_original": score,
                "score_adjusted": score,
                "multiplier":     multiplier,
                "adjustment_pct": 0.0,
            }

        score_adjusted = round(score * combined, 1)
        adjustment_pct = round((combined - 1.0) * 100, 1)

        return {
            "score_original": score,
            "score_adjusted": score_adjusted,
            "multiplier":     multiplier,
            "adjustment_pct": adjustment_pct,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _generate_context_label(self, day_m: float, hour_m: float) -> str:
        """Gera descrição legível do contexto sazonal combinado."""
        combined = day_m * hour_m
        if   combined >= 1.15: return "Muito favorável — alta confluência temporal"
        elif combined >= 1.05: return "Favorável — condições sazonais positivas"
        elif combined >= 0.95: return "Neutro — sem viés sazonal significativo"
        elif combined >= 0.85: return "Desfavorável — baixa liquidez histórica"
        else:                  return "Evitar — padrão histórico muito fraco"

    @property
    def data_available(self) -> bool:
        """True se seasonality_data.json foi carregado com sucesso."""
        return self._data is not None

    @property
    def generated_at(self) -> Optional[str]:
        """ISO timestamp de geração dos dados, ou None se não disponível."""
        return self._data.get("generated_at") if self._data else None

    @property
    def top_combinations(self) -> list:
        """Top combinations do arquivo de dados, ou lista vazia."""
        return self._data.get("top_combinations", []) if self._data else []
