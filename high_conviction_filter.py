"""
high_conviction_filter.py — Filtro de alta convicção para sinais institucionais.
Só aprova sinais com confluência real de múltiplas condições SMC.
Meta: menos sinais, precisão ≥ 90%.

Campos lidos de smc_analysis (output de SmartMoneyEngine.analyze()):
  smc_analysis["alignment"]                                    → MTF alignment
  smc_analysis["mtf"]["details"]["4H"]["bos_bull"|"bos_bear"] → BOS no 4H
  smc_analysis["score"]["layer_scores"]["volume_confirm"]      → volume layer
  smc_analysis["order_blocks"]["bullish_ob"]["valid"]          → OB válido
  smc_analysis["order_blocks"]["bearish_ob"]["valid"]          → OB válido
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
SCORE_MIN: dict = {"LONG": 65, "SHORT": 62}
HIGH_SCORE: dict = {"LONG": 72, "SHORT": 70}   # tier HIGH exige score ainda maior
VOLUME_THRESHOLD: float = 55.0                  # volume_confirm layer > N para "acima da média"
FUNDING_CACHE_TTL: int  = 600                   # 10 min de cache para funding rate
REJECTED_LOG_DIR = Path("logs")

# Condições obrigatórias mapeadas para os campos reais do smc_analysis
LONG_REQUIRED = [
    "mtf_aligned_bullish",   # 1D e/ou 4H bullish (do run_mtf_smc_analysis)
    "bos_bullish_4h",        # Break of Structure bullish confirmado no 4H
    "volume_above_avg",      # volume_confirm score > VOLUME_THRESHOLD
    "valid_order_block",     # ao menos 1 OB bullish válido
]

SHORT_REQUIRED = [
    "mtf_aligned_bearish",   # 1D e/ou 4H bearish
    "bos_bearish_4h",        # BOS bearish confirmado no 4H
    "volume_above_avg",      # idem
    "funding_rate_positive", # longs pagando (mercado sobrecomprado)
]

# ── Singleton ─────────────────────────────────────────────────────────────────
_instance: Optional["HighConvictionFilter"] = None
_funding_cache: dict = {"rate": None, "cached_at": 0}


def get_filter() -> "HighConvictionFilter":
    """Retorna instância singleton do HighConvictionFilter."""
    global _instance
    if _instance is None:
        _instance = HighConvictionFilter()
    return _instance


class HighConvictionFilter:
    """Filtro de alta convicção: aprova sinais apenas com confluência SMC real."""

    def evaluate(self, signal: dict, smc_analysis: dict) -> dict:
        """
        Retorna:
        {
          "approved": bool,
          "direction": "LONG" | "SHORT" | "NO_TRADE",
          "score": float,
          "conviction_tier": "HIGH" | "MEDIUM" | "BLOCKED",
          "conditions_met": [...],
          "conditions_failed": [...],
          "rejection_reason": str | None
        }
        Lógica: score < mínimo → BLOCKED imediatamente.
        Condição faltando → BLOCKED com lista de conditions_failed.
        """
        direction = (signal.get("direction") or "").upper().strip()
        score     = float(signal.get("score") or 0)

        base = {
            "approved":          False,
            "direction":         direction,
            "score":             score,
            "conviction_tier":   "BLOCKED",
            "conditions_met":    [],
            "conditions_failed": [],
            "rejection_reason":  None,
        }

        if direction not in ("LONG", "SHORT"):
            base["rejection_reason"] = f"direção inválida: {direction}"
            return base

        # ── Verificação de score mínimo ───────────────────────────────────────
        min_score = SCORE_MIN.get(direction, 65)
        if score < min_score:
            base["rejection_reason"] = (
                f"score {score:.1f} abaixo do mínimo {min_score} para {direction}"
            )
            return base

        # ── Avalia condições estruturais ──────────────────────────────────────
        conditions = self._check_conditions(direction, smc_analysis)
        conditions_met    = [c for c, ok in conditions.items() if ok]
        conditions_failed = [c for c, ok in conditions.items() if not ok]

        required = LONG_REQUIRED if direction == "LONG" else SHORT_REQUIRED

        missing = [c for c in required if c in conditions_failed]
        if missing:
            base["conditions_met"]    = conditions_met
            base["conditions_failed"] = conditions_failed
            base["rejection_reason"]  = f"condições não atendidas: {missing}"
            return base

        # ── Aprovado — determina tier ─────────────────────────────────────────
        high_score = HIGH_SCORE.get(direction, 72)
        tier = "HIGH" if (score >= high_score and not conditions_failed) else "MEDIUM"

        return {
            "approved":          True,
            "direction":         direction,
            "score":             score,
            "conviction_tier":   tier,
            "conditions_met":    conditions_met,
            "conditions_failed": conditions_failed,
            "rejection_reason":  None,
        }

    def _check_conditions(self, direction: str, smc: dict) -> dict:
        """Avalia todas as condições e retorna dict {condição: bool}."""
        # Acesso seguro à estrutura do smc_analysis
        alignment    = smc.get("alignment", "MIXED")
        mtf_details  = smc.get("mtf", {}).get("details", {}) if isinstance(smc.get("mtf"), dict) else {}
        four_h       = mtf_details.get("4H", {})
        one_d        = mtf_details.get("1D", {})
        layer_scores = smc.get("score", {}).get("layer_scores", {}) if isinstance(smc.get("score"), dict) else {}
        ob           = smc.get("order_blocks", {})
        bullish_ob   = ob.get("bullish_ob") or {}
        bearish_ob   = ob.get("bearish_ob") or {}

        vol_confirm  = float(layer_scores.get("volume_confirm", 50))
        volume_ok    = vol_confirm > VOLUME_THRESHOLD

        # MTF alignment
        mtf_bullish = alignment in ("BULLISH", "BULLISH_WEAK")
        mtf_bearish = alignment in ("BEARISH", "BEARISH_WEAK")

        # BOS no 4H e 1D
        bos_bull_4h = bool(four_h.get("bos_bull", False))
        bos_bear_4h = bool(four_h.get("bos_bear", False))

        # Order blocks válidos
        ob_bull_valid = bool(bullish_ob.get("valid", False))
        ob_bear_valid = bool(bearish_ob.get("valid", False))

        # Funding rate (Binance) — cache 10 min
        funding = self.get_funding_rate()
        # Positivo = longs pagando = sobrecomprado = sinal SHORT
        funding_positive = (funding is not None and funding > 0.0001)

        if direction == "LONG":
            return {
                "mtf_aligned_bullish": mtf_bullish,
                "bos_bullish_4h":      bos_bull_4h,
                "volume_above_avg":    volume_ok,
                "valid_order_block":   ob_bull_valid,
            }
        else:  # SHORT
            return {
                "mtf_aligned_bearish":  mtf_bearish,
                "bos_bearish_4h":       bos_bear_4h,
                "volume_above_avg":     volume_ok,
                "funding_rate_positive": funding_positive,
            }

    def get_funding_rate(self, symbol: str = "BTCUSDT") -> Optional[float]:
        """
        GET https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}
        Retorna lastFundingRate como float. Cache de 10 min em memória.
        Retorna None silenciosamente em caso de erro.
        Funding positivo = longs pagando = mercado sobrecomprado = SHORT válido.
        """
        import time
        import requests
        global _funding_cache

        now = time.time()
        if _funding_cache["rate"] is not None and (now - _funding_cache["cached_at"]) < FUNDING_CACHE_TTL:
            return _funding_cache["rate"]

        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": symbol},
                timeout=5,
            )
            r.raise_for_status()
            data = r.json()
            rate = float(data.get("lastFundingRate", 0))
            _funding_cache = {"rate": rate, "cached_at": now}
            return rate
        except Exception as e:
            log.debug(f"[HCF] Funding rate fetch falhou ({symbol}): {e}")
            return None

    def log_rejection(self, signal: dict, evaluation: dict):
        """
        Salva em logs/rejected_signals_YYYY-MM-DD.jsonl para análise futura.
        Campos: timestamp, direction, score, rejection_reason, conditions_failed.
        """
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = REJECTED_LOG_DIR / f"rejected_signals_{today}.jsonl"
            log_file.parent.mkdir(exist_ok=True)
            entry = {
                "timestamp":         datetime.now(timezone.utc).isoformat(),
                "direction":         signal.get("direction", ""),
                "score":             signal.get("score", 0),
                "rejection_reason":  evaluation.get("rejection_reason", ""),
                "conditions_failed": evaluation.get("conditions_failed", []),
                "conditions_met":    evaluation.get("conditions_met", []),
            }
            with log_file.open("a") as f:
                f.write(json.dumps(entry) + "\n")
            log.info(
                f"[HCF] Sinal rejeitado: {entry['direction']} score={entry['score']:.1f} "
                f"| {entry['rejection_reason']}"
            )
        except Exception as e:
            log.error(f"[HCF] Erro ao logar rejeição: {e}")

    def format_telegram_alert(self, evaluation: dict, signal: dict) -> str:
        """
        Formata mensagem Telegram com conviction tier, conditions e levels.
        """
        tier       = evaluation.get("conviction_tier", "MEDIUM")
        direction  = evaluation.get("direction", "")
        score      = evaluation.get("score", 0)
        conditions = evaluation.get("conditions_met", [])

        prefix = "🔥 HIGH CONVICTION" if tier == "HIGH" else "✅ MEDIUM CONVICTION"
        dir_emoji = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
        validity  = "8H" if tier == "HIGH" else "4H"

        entry = signal.get("entry_price", signal.get("entry", "?"))
        stop  = signal.get("stop_loss",  signal.get("stop", "?"))
        tp1   = signal.get("tp1", "?")
        tp2   = signal.get("tp2", "?")

        conds_str = "\n".join(f"  ✓ {c}" for c in conditions[:4])

        msg = (
            f"{prefix}\n"
            f"{dir_emoji} | BTC/USDT | Score: {score:.1f}/100\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📍 Entry:  ${entry:,.2f}\n"
            f"🛑 Stop:   ${stop:,.2f}\n"
            f"🎯 TP1:    ${tp1:,.2f}\n"
            f"🎯 TP2:    ${tp2:,.2f}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Confluências:\n{conds_str}\n"
            f"⏱ Validade estimada: {validity}"
        ) if isinstance(entry, (int, float)) else (
            f"{prefix}\n"
            f"{dir_emoji} | Score: {score:.1f}/100\n"
            f"Confluências:\n{conds_str}\n"
            f"⏱ Validade estimada: {validity}"
        )
        return msg
