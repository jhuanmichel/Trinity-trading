"""
outcome_tracker.py — Rastreia outcomes de sinais institucionais gerados.
Registra sinais pendentes e verifica automaticamente TP1/TP2/STOP/EXPIRADO.

Persistência:
  logs/pending_outcomes.jsonl  — sinais aguardando resolução
  logs/outcomes_YYYY-MM.jsonl  — sinais resolvidos por mês
  dashboard/win_rate.json      — cache de métricas (lido pelo dashboard)
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
# Render Persistent Disk montado em /data — usa /data/logs quando disponível.
# Fallback para logs/ em dev local (quando /data não existe).
OUTCOMES_DIR     = Path("/data/logs") if Path("/data").exists() else Path("logs")
OUTCOMES_DIR.mkdir(parents=True, exist_ok=True)
PENDING_FILE     = OUTCOMES_DIR / "pending_outcomes.jsonl"
WIN_RATE_FILE    = Path("dashboard/win_rate.json")
log.info("[OUTCOME] Usando %s (%s)", OUTCOMES_DIR,
         "persistent disk" if str(OUTCOMES_DIR).startswith("/data") else "dev local")
MEXC_FUTURES_KLINES = "https://contract.mexc.com/api/v1/contract/kline"  # Futures (não spot)
CHECK_AFTER_H    = [4, 24, 48]   # checkpoints em horas após o sinal
EXPIRY_HOURS     = 48            # horas até expirar automaticamente
MIN_SAMPLES_DISPLAY = 5         # mínimo de amostras WIN+LOSS para exibir win_rate

# ── Singleton ─────────────────────────────────────────────────────────────────
_instance: Optional["OutcomeTracker"] = None


def get_tracker() -> "OutcomeTracker":
    """Retorna instância singleton do OutcomeTracker."""
    global _instance
    if _instance is None:
        _instance = OutcomeTracker()
    return _instance


class OutcomeTracker:
    """Registra e verifica outcomes de sinais SMC institucionais."""

    def register_signal(self, signal: dict) -> Optional[str]:
        """
        Salva sinal em pending_outcomes.jsonl.
        Retorna signal_id gerado, ou None se direction for NO_TRADE/NEUTRO.
        signal deve conter: direction, score, entry_price, stop_loss,
        tp1, tp2, symbol, timestamp (ISO 8601).
        Adiciona campo 'signal_id' (uuid4) e 'registered_at'.
        """
        if signal.get("direction") in ("NO_TRADE", "NEUTRO"):
            return None
        signal_id = str(uuid.uuid4())
        entry = {
            "signal_id":      signal_id,
            "registered_at":  datetime.now(timezone.utc).isoformat(),
            "direction":      signal.get("direction", ""),
            "score":          signal.get("score", 0),
            "entry_price":    signal.get("entry_price", 0),
            "stop_loss":      signal.get("stop_loss"),
            "tp1":            signal.get("tp1"),
            "tp2":            signal.get("tp2"),
            "symbol":         signal.get("symbol", "BTCUSDT"),
            "timestamp":      signal.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "conviction_tier": signal.get("conviction_tier", "MEDIUM"),
            "layer_scores":   signal.get("layer_scores", {}),
        }
        try:
            PENDING_FILE.parent.mkdir(exist_ok=True)
            with PENDING_FILE.open("a") as f:
                f.write(json.dumps(entry) + "\n")
            log.info(
                f"[OutcomeTracker] Sinal registrado: {signal_id[:8]} | "
                f"{entry['direction']} @ {entry['entry_price']} | "
                f"tier={entry['conviction_tier']}"
            )
        except Exception as e:
            log.error(f"[OutcomeTracker] Erro ao registrar sinal: {e}")
        return signal_id

    def check_pending_outcomes(self) -> list:
        """
        Para cada sinal pendente:
          1. Busca OHLCV do símbolo via MEXC desde o timestamp do sinal.
          2. Verifica candles HIGH e LOW para detectar toque em TP1, TP2, stop.
          3. Prioridade: stop > TP2 > TP1 (conservador — se stop e TP tocaram
             no mesmo candle, considera stop).
          4. Se passou EXPIRY_HOURS sem toque: outcome = EXPIRED.
        Retorna lista de outcomes resolvidos nesta rodada.
        """
        if not PENDING_FILE.exists():
            return []

        try:
            lines = [l.strip() for l in PENDING_FILE.read_text().splitlines() if l.strip()]
        except Exception as e:
            log.error(f"[OutcomeTracker] Erro ao ler pendentes: {e}")
            return []

        pending  = []
        resolved = []

        for line in lines:
            try:
                s = json.loads(line)
            except Exception:
                continue

            outcome = self._evaluate_outcome(s)
            if outcome is None:
                pending.append(s)
            else:
                self.save_outcome(s["signal_id"], outcome["outcome"], outcome)
                resolved.append(outcome)

        # Reescreve arquivo apenas com pendentes
        try:
            content = "\n".join(json.dumps(p) for p in pending)
            PENDING_FILE.write_text(content + "\n" if pending else "")
        except Exception as e:
            log.error(f"[OutcomeTracker] Erro ao reescrever pendentes: {e}")

        if resolved:
            log.info(f"[OutcomeTracker] {len(resolved)} outcome(s) resolvido(s) nesta rodada")
        return resolved

    def _evaluate_outcome(self, s: dict) -> Optional[dict]:
        """Avalia sinal pendente. Retorna dict com outcome ou None se ainda pendente."""
        try:
            ts_str = s.get("timestamp", s.get("registered_at", ""))
            ts     = self._parse_ts(ts_str)
            if ts is None:
                return None

            now   = datetime.now(timezone.utc)
            age_h = (now - ts).total_seconds() / 3600

            if age_h < 1.0:
                return None   # Muito cedo — aguarda pelo menos 1h

            # Expirado
            if age_h >= EXPIRY_HOURS:
                return self._make_outcome("EXPIRED", "NEUTRAL", s, now, None)

            # Busca candles
            candles = self._fetch_candles_since(s.get("symbol", "BTCUSDT"), ts_str)
            if not candles:
                return None

            direction = s.get("direction", "LONG")
            entry     = float(s.get("entry_price", 0) or 0)
            stop_loss = s.get("stop_loss")
            tp1       = s.get("tp1")
            tp2       = s.get("tp2")

            if not entry or not stop_loss:
                return None

            stop_loss = float(stop_loss)
            tp1_f     = float(tp1) if tp1 else None
            tp2_f     = float(tp2) if tp2 else None

            for candle in candles:
                high = float(candle[2])
                low  = float(candle[3])

                if direction == "LONG":
                    # Prioridade: stop > TP2 > TP1
                    if low <= stop_loss:
                        return self._make_outcome("STOP_HIT", "LOSS", s, now, stop_loss)
                    if tp2_f and high >= tp2_f:
                        return self._make_outcome("TP2_HIT", "WIN", s, now, tp2_f)
                    if tp1_f and high >= tp1_f:
                        return self._make_outcome("TP1_HIT", "WIN", s, now, tp1_f)

                elif direction == "SHORT":
                    if high >= stop_loss:
                        return self._make_outcome("STOP_HIT", "LOSS", s, now, stop_loss)
                    if tp2_f and low <= tp2_f:
                        return self._make_outcome("TP2_HIT", "WIN", s, now, tp2_f)
                    if tp1_f and low <= tp1_f:
                        return self._make_outcome("TP1_HIT", "WIN", s, now, tp1_f)

            return None   # Nenhum nível tocado ainda

        except Exception as e:
            log.error(f"[OutcomeTracker] Erro ao avaliar {s.get('signal_id','?')}: {e}")
            return None

    def _make_outcome(
        self, outcome: str, status: str, s: dict, now: datetime, exit_price: Optional[float]
    ) -> dict:
        """Monta dict de outcome resolvido."""
        return {
            "outcome":        outcome,
            "status":         status,
            "signal_id":      s.get("signal_id", ""),
            "symbol":         s.get("symbol", ""),
            "direction":      s.get("direction", ""),
            "score":          s.get("score", 0),
            "layer_scores":   s.get("layer_scores", {}),
            "conviction_tier": s.get("conviction_tier", "MEDIUM"),
            "entry":          s.get("entry_price", 0),
            "stop_loss":      s.get("stop_loss"),
            "tp1":            s.get("tp1"),
            "tp2":            s.get("tp2"),
            "exit_price":     exit_price,
            "registered_at":  s.get("registered_at", ""),
            "resolved_at":    now.isoformat(),
        }

    def _parse_ts(self, ts_str: str) -> Optional[datetime]:
        """Faz parse de ISO 8601 com ou sem timezone."""
        if not ts_str:
            return None
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        except Exception:
            return None

    def _fetch_candles_since(self, symbol: str, since_ts: str, interval: str = "Min15") -> list:
        """Busca OHLCV do MEXC Futures desde o timestamp do sinal até agora.

        Usa a API de futuros (contract.mexc.com) porque os símbolos scaneados
        (WETUSDT, SOLUSDT, etc.) são contratos futuros — não existem no spot.
        """
        import requests
        try:
            ts = self._parse_ts(since_ts)
            if ts is None:
                return []
            start_s = int(ts.timestamp())  # Futures API usa segundos (não ms)

            # Normaliza → "WETUSDT" → "WET_USDT" (formato MEXC Futures)
            sym = (symbol
                   .replace("/USDT:USDT", "USDT")
                   .replace("/USDT", "USDT")
                   .replace("/", "")
                   .replace(":USDT", ""))
            if not sym.endswith("USDT"):
                sym = sym + "USDT"
            # Adiciona underscore se necessário: "WETUSDT" → "WET_USDT"
            if "_" not in sym and sym.endswith("USDT"):
                sym = sym[:-4] + "_USDT"

            r = requests.get(
                f"{MEXC_FUTURES_KLINES}/{sym}",
                params={"interval": interval, "start": start_s, "count": 200},
                timeout=5,
            )
            r.raise_for_status()
            body = r.json()

            if not body.get("success") or not body.get("data"):
                log.debug(f"[OutcomeTracker] Futures kline vazia para {sym}: {body.get('message','')}")
                return []

            d      = body["data"]
            times  = d.get("time",  [])
            highs  = d.get("high",  [])
            lows   = d.get("low",   [])
            opens  = d.get("open",  [])
            closes = d.get("close", [])

            # Converte para formato candle: [time, open, high, low, close]
            candles = []
            for i in range(len(times)):
                try:
                    candles.append([
                        times[i],
                        float(opens[i]),
                        float(highs[i]),
                        float(lows[i]),
                        float(closes[i]),
                    ])
                except (IndexError, ValueError, TypeError):
                    continue
            return candles

        except Exception as e:
            log.warning(f"[OutcomeTracker] OHLCV fetch falhou ({symbol}): {e}")
            return []

    def save_outcome(self, signal_id: str, outcome: str, detail: dict):
        """
        outcome ∈ {"TP1_HIT", "TP2_HIT", "STOP_HIT", "EXPIRED"}
        status  ∈ {"WIN", "WIN", "LOSS", "NEUTRAL"}
        Move do pending para logs/outcomes_YYYY-MM.jsonl.
        Chama _update_win_rate_cache() após salvar.
        """
        try:
            month    = datetime.now().strftime("%Y-%m")
            out_file = OUTCOMES_DIR / f"outcomes_{month}.jsonl"
            out_file.parent.mkdir(exist_ok=True)
            with out_file.open("a") as f:
                f.write(json.dumps(detail) + "\n")
            log.info(f"[OutcomeTracker] Outcome salvo: {signal_id[:8]} → {outcome}")
            self._update_win_rate_cache()
        except Exception as e:
            log.error(f"[OutcomeTracker] Erro ao salvar outcome: {e}")

    def _update_win_rate_cache(self):
        """
        Recalcula e salva dashboard/win_rate.json com métricas de acerto.
        """
        try:
            outcomes = self._load_all_outcomes()

            wins    = [o for o in outcomes if o.get("status") == "WIN"]
            losses  = [o for o in outcomes if o.get("status") == "LOSS"]
            neutral = [o for o in outcomes if o.get("status") == "NEUTRAL"]

            n_wins  = len(wins)
            n_loss  = len(losses)
            n_neut  = len(neutral)
            total   = len(outcomes)
            usable  = n_wins + n_loss

            wr = round(n_wins / usable * 100, 1) if usable >= 5 else None

            avg_score_wins   = (round(sum(o.get("score", 0) for o in wins)   / n_wins,  1)
                                if wins   else None)
            avg_score_losses = (round(sum(o.get("score", 0) for o in losses) / n_loss,  1)
                                if losses else None)

            # Melhor direção
            best_dir = self._best_direction(outcomes)

            # Breakdown por conviction_tier
            # Tiers reais: EXTREME/STRONG/TRADEABLE/WEAK/MICRO (move_classification)
            by_conviction_tier: dict = {}
            for tier in ("EXTREME", "STRONG", "TRADEABLE", "WEAK", "MICRO"):
                tier_outs   = [o for o in outcomes if o.get("conviction_tier") == tier]
                tier_wins   = sum(1 for o in tier_outs if o.get("status") == "WIN")
                tier_losses = sum(1 for o in tier_outs if o.get("status") == "LOSS")
                tier_wl     = tier_wins + tier_losses
                by_conviction_tier[tier] = {
                    "win_rate_pct": round(tier_wins / tier_wl * 100, 1) if tier_wl >= 3 else None,
                    "count": len(tier_outs),
                }

            cache = {
                "updated_at":         datetime.now(timezone.utc).isoformat(),
                "total_signals":      total,
                "wins":               n_wins,
                "losses":             n_loss,
                "neutral":            n_neut,
                "win_rate_pct":       wr,
                "win_rate_display":   f"{wr:.1f}%" if wr is not None else "Dados insuficientes",
                "avg_score_wins":     avg_score_wins,
                "avg_score_losses":   avg_score_losses,
                "best_direction":     best_dir,
                "by_conviction_tier": by_conviction_tier,
                "optimizer_progress": {
                    "samples_collected": usable,
                    "samples_needed":    30,
                    "pct_complete":      round(min(usable / 30 * 100, 100)),
                    "ready":             usable >= 30,
                },
            }

            WIN_RATE_FILE.parent.mkdir(exist_ok=True)
            WIN_RATE_FILE.write_text(json.dumps(cache, indent=2))

        except Exception as e:
            log.error(f"[OutcomeTracker] Erro ao atualizar win_rate.json: {e}")

    def _best_direction(self, outcomes: list) -> Optional[str]:
        """Detecta a direção com maior win rate (mínimo 3 amostras por direção)."""
        for direction in ("LONG", "SHORT"):
            d_wins = sum(1 for o in outcomes if o.get("direction") == direction and o.get("status") == "WIN")
            d_loss = sum(1 for o in outcomes if o.get("direction") == direction and o.get("status") == "LOSS")
            if d_wins + d_loss < 3:
                return None

        long_wins   = sum(1 for o in outcomes if o.get("direction") == "LONG"  and o.get("status") == "WIN")
        long_total  = sum(1 for o in outcomes if o.get("direction") == "LONG"  and o.get("status") in ("WIN","LOSS"))
        short_wins  = sum(1 for o in outcomes if o.get("direction") == "SHORT" and o.get("status") == "WIN")
        short_total = sum(1 for o in outcomes if o.get("direction") == "SHORT" and o.get("status") in ("WIN","LOSS"))

        if long_total < 3 or short_total < 3:
            return None

        long_wr  = long_wins  / long_total
        short_wr = short_wins / short_total

        if long_wr > short_wr + 0.10:  return "LONG"
        if short_wr > long_wr  + 0.10: return "SHORT"
        return None

    def _load_all_outcomes(self) -> list:
        """Carrega todos os outcomes_YYYY-MM.jsonl existentes."""
        outcomes: list = []
        for p in sorted(OUTCOMES_DIR.glob("outcomes_*.jsonl")):
            try:
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if line:
                        outcomes.append(json.loads(line))
            except Exception as e:
                log.warning(f"[OutcomeTracker] Erro ao ler {p.name}: {e}")
        return outcomes

    def get_stats(self) -> dict:
        """Lê e retorna win_rate.json. Cria com zeros se não existir."""
        if WIN_RATE_FILE.exists():
            try:
                return json.loads(WIN_RATE_FILE.read_text())
            except Exception:
                pass
        # Cria arquivo inicial zerado
        empty = {
            "updated_at":         datetime.now(timezone.utc).isoformat(),
            "total_signals":      0,
            "wins":               0,
            "losses":             0,
            "neutral":            0,
            "win_rate_pct":       None,
            "win_rate_display":   "Dados insuficientes",
            "avg_score_wins":     None,
            "avg_score_losses":   None,
            "best_direction":     None,
            "by_conviction_tier": {
                "EXTREME":   {"win_rate_pct": None, "count": 0},
                "STRONG":    {"win_rate_pct": None, "count": 0},
                "TRADEABLE": {"win_rate_pct": None, "count": 0},
                "WEAK":      {"win_rate_pct": None, "count": 0},
                "MICRO":     {"win_rate_pct": None, "count": 0},
            },
            "optimizer_progress": {
                "samples_collected": 0,
                "samples_needed":    30,
                "pct_complete":      0,
                "ready":             False,
            },
        }
        try:
            WIN_RATE_FILE.parent.mkdir(exist_ok=True)
            WIN_RATE_FILE.write_text(json.dumps(empty, indent=2))
        except Exception:
            pass
        return empty
