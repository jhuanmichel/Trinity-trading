"""
market_movers.py — Market Movers Scanner v1

Escaneia todos os 800+ contratos MEXC Futures a cada 10 minutos.
Detecta movimentos de preço ≥5% em 24h e classifica em tiers.

Tiers:
  PARABOLIC  ≥60%  → Telegram + Deep Dive + crash watchlist
  EXTREME    ≥30%  → Telegram + crash watchlist (se em alta)
  STRONG     ≥15%  → Telegram
  MOVER      ≥5%   → apenas OutcomeTracker (sem Telegram) — ML data

Gera 50-100 outcomes/dia para alimentar o modelo de ML.
"""
import logging
import threading
import time
from typing import Dict, List, Optional

import requests

log = logging.getLogger(__name__)

# ── Configuração ───────────────────────────────────────────────────────────
# MEXC Spot 24h ticker — api.mexc.com não sofre geo-blocking no Render
# (contract.mexc.com/api/v1/contract/ticker retorna 403 de IPs cloud)
MEXC_TICKER_URL = "https://api.mexc.com/api/v3/ticker/24hr"
MIN_VOLUME_USDT = 500_000  # ignorar pares com volume < 500k USD 24h

# Limites de variação absoluta 24h por tier
TIER_THRESHOLDS = {
    "PARABOLIC": 60.0,
    "EXTREME":   30.0,
    "STRONG":    15.0,
    "MOVER":      5.0,
}

# Cooldown por símbolo por tier (segundos) — evita re-alertar o mesmo tier
_COOLDOWN_S = {
    "PARABOLIC": 1800,   # 30 min
    "EXTREME":   3600,   # 1h
    "STRONG":    3600,   # 1h
    "MOVER":     7200,   # 2h (só OutcomeTracker)
}

# Tiers que enviam Telegram
_TELEGRAM_TIERS = {"PARABOLIC", "EXTREME", "STRONG"}


# ── MarketMoversScanner ────────────────────────────────────────────────────

class MarketMoversScanner:
    """Sensor de price action puro — detecta movers por variação 24h."""

    def __init__(self):
        self._lock      = threading.Lock()
        self._cooldowns: Dict[tuple, float] = {}
        self._state: dict = {
            "status":          "idle",
            "last_scan_ts":    0.0,
            "last_scan_count": 0,
            "elapsed_s":       0.0,
            "alerts_sent":     0,
            "trending":        [],
            "crash_watchlist": [],
            "tiers":           {"PARABOLIC": 0, "EXTREME": 0, "STRONG": 0, "MOVER": 0},
        }

    # ── Helpers internos ───────────────────────────────────────────────────

    @staticmethod
    def _get_tier(change_pct: float) -> Optional[str]:
        """Retorna tier baseado na variação absoluta 24h."""
        a = abs(change_pct)
        if a >= TIER_THRESHOLDS["PARABOLIC"]: return "PARABOLIC"
        if a >= TIER_THRESHOLDS["EXTREME"]:   return "EXTREME"
        if a >= TIER_THRESHOLDS["STRONG"]:    return "STRONG"
        if a >= TIER_THRESHOLDS["MOVER"]:     return "MOVER"
        return None

    def _in_cooldown(self, symbol: str, tier: str) -> bool:
        key  = (symbol, tier)
        last = self._cooldowns.get(key, 0.0)
        return (time.time() - last) < _COOLDOWN_S.get(tier, 3600)

    def _set_cooldown(self, symbol: str, tier: str) -> None:
        self._cooldowns[(symbol, tier)] = time.time()
        # Limpar entradas expiradas quando acumular muitas
        if len(self._cooldowns) > 3000:
            cutoff = time.time() - max(_COOLDOWN_S.values()) * 1.5
            expired = [k for k, v in self._cooldowns.items() if v < cutoff]
            for k in expired:
                del self._cooldowns[k]

    @staticmethod
    def _fetch_velocity(symbol: str) -> str:
        """
        Calcula velocidade do movimento nas últimas 2h (8 klines × 15min).
        Retorna: FAST / STEADY / SLOWING / REVERSING / UNKNOWN
        """
        try:
            from trinity.exchanges.mexc_adapter import MexcAdapter
            klines = MexcAdapter().fetch_klines(symbol, interval="15m", limit=8)
            if not klines or len(klines) < 6:
                return "UNKNOWN"

            closes = []
            for k in klines:
                c = k.get("close", k.get("c", 0))
                try:
                    closes.append(float(c))
                except (TypeError, ValueError):
                    pass

            if len(closes) < 6:
                return "UNKNOWN"

            # Primeira metade (t-2h → t-1h) vs segunda metade (t-1h → agora)
            mid = len(closes) // 2
            c0_start, c0_end = closes[0], closes[mid - 1]
            c1_start, c1_end = closes[mid], closes[-1]

            chg0 = (c0_end - c0_start) / c0_start * 100 if c0_start else 0.0
            chg1 = (c1_end - c1_start) / c1_start * 100 if c1_start else 0.0

            # Reversão: direções opostas com magnitude relevante
            if chg0 > 0.5 and chg1 < -0.5:
                return "REVERSING"
            if chg0 < -0.5 and chg1 > 0.5:
                return "REVERSING"

            abs0, abs1 = abs(chg0), abs(chg1)
            if abs1 > abs0 * 1.3:
                return "FAST"
            if abs0 > 0 and abs1 < abs0 * 0.5:
                return "SLOWING"
            return "STEADY"

        except Exception as e:
            log.debug(f"[Movers] velocity {symbol}: {e}")
            return "UNKNOWN"

    @staticmethod
    def _format_telegram(symbol: str, tier: str, change_pct: float,
                          price: float, volume_usdt: float,
                          velocity: str, btc_bias: str) -> str:
        """Formata mensagem Telegram para mover."""
        direction = "UP" if change_pct > 0 else "DOWN"
        sign      = "+" if change_pct > 0 else ""
        emoji_dir = "🚀" if direction == "UP" else "💥"

        tier_icons = {
            "PARABOLIC": "🔥🔥🔥",
            "EXTREME":   "⚡⚡",
            "STRONG":    "📈" if direction == "UP" else "📉",
        }
        icon = tier_icons.get(tier, "📊")

        vol_str = (
            f"${volume_usdt / 1_000_000:.1f}M"
            if volume_usdt >= 1_000_000
            else f"${volume_usdt / 1_000:.0f}K"
        )

        vel_labels = {
            "FAST":      "⚡ Acelerando",
            "STEADY":    "→ Constante",
            "SLOWING":   "⬇ Desacelerando",
            "REVERSING": "⚠️ Revertendo",
            "UNKNOWN":   "— N/D",
        }
        vel_str = vel_labels.get(velocity, velocity)

        sym_clean = symbol.replace("_USDT", "")

        # Formatação de preço
        if price >= 1000:
            price_str = f"${price:,.2f}"
        elif price >= 1:
            price_str = f"${price:.4f}"
        elif price >= 0.01:
            price_str = f"${price:.6f}"
        else:
            price_str = f"${price:.8f}"

        return (
            f"{icon} <b>MARKET MOVER — {tier}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{emoji_dir} <b>${sym_clean}</b>  "
            f"<b>{sign}{change_pct:.1f}%</b>\n"
            f"Preço:      {price_str}\n"
            f"Volume 24h: {vol_str}\n"
            f"Velocidade: {vel_str}\n"
            f"BTC Regime: {btc_bias}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Market Movers Scanner — Trinity v7</i>"
        )

    @staticmethod
    def _register_outcome(symbol: str, tier: str, change_pct: float,
                           price: float, direction: str, volume_usdt: float) -> None:
        """Registra sinal no OutcomeTracker para geração de dados de ML."""
        try:
            from outcome_tracker import get_tracker
            signal = {
                "symbol":      symbol,
                "direction":   "LONG" if direction == "UP" else "SHORT",
                "type":        "market_mover",
                "tier":        tier,
                "change_pct":  change_pct,
                "entry":       price,
                "volume_usdt": volume_usdt,
                "source":      "market_movers_scanner",
                "timestamp":   time.time(),
            }
            get_tracker().register_signal(signal)
        except Exception as e:
            log.debug(f"[Movers] OutcomeTracker {symbol}: {e}")

    # ── Método principal ───────────────────────────────────────────────────

    def scan(self) -> dict:
        """
        Varredura completa — chamada a cada 10 minutos pelo scheduler.
        Retorna cópia do estado atualizado.
        """
        t0 = time.time()
        log.info("[Movers] Iniciando varredura de mercado...")

        with self._lock:
            self._state["status"] = "scanning"

        # ── 1. Tickers MEXC ───────────────────────────────────────────────
        try:
            # api.mexc.com/v3 (Spot) — não bloqueado por geo-IP no Render
            resp = requests.get(MEXC_TICKER_URL, timeout=20)
            resp.raise_for_status()
            raw = resp.json()
            # Resposta é lista plana (sem wrapper {"data": [...]})
            tickers = raw if isinstance(raw, list) else raw.get("data", [])
        except Exception as e:
            log.error(f"[Movers] Falha ao buscar tickers MEXC: {e}")
            with self._lock:
                self._state["status"] = f"error: {e}"
            return {}

        if not tickers:
            log.warning("[Movers] Nenhum ticker retornado — MEXC vazio")
            return {}

        # ── 2. BTC Regime (contexto) ──────────────────────────────────────
        try:
            from trinity.traders.btc_regime_monitor import get_btc_regime
            btc      = get_btc_regime()
            btc_bias = btc.get("bias", "NEUTRAL")
        except Exception:
            btc_bias = "NEUTRAL"

        # ── 3. Processar tickers ──────────────────────────────────────────
        movers:            List[dict] = []
        crash_watchlist:   List[dict] = []
        alerts_sent        = 0
        tier_counts        = {t: 0 for t in TIER_THRESHOLDS}

        for item in tickers:
            try:
                # Spot API retorna símbolo sem underscore: "SOLUSDT" → "SOL_USDT"
                sym_raw = item.get("symbol", "")
                if not sym_raw.endswith("USDT"):
                    continue
                sym = sym_raw[:-4] + "_USDT"  # "SOLUSDT" → "SOL_USDT"

                price    = float(item.get("lastPrice",          0) or 0)
                pcp      = float(item.get("priceChangePercent", 0) or 0)
                amount24 = float(item.get("quoteVolume",        0) or 0)
                funding  = 0.0   # não disponível na Spot API
                hold_vol = 0.0   # OI não disponível na Spot API

                if price <= 0 or amount24 < MIN_VOLUME_USDT:
                    continue

                change_pct = pcp * 100   # priceChangePercent é decimal (ex: -0.0373 → -3.73%)
                tier = self._get_tier(change_pct)
                if not tier:
                    continue

                direction = "UP" if change_pct > 0 else "DOWN"
                oi_usd    = hold_vol * price
                tier_counts[tier] += 1

                mover_entry = {
                    "symbol":      sym,
                    "price":       price,
                    "change_pct":  round(change_pct, 2),
                    "volume_usdt": round(amount24, 0),
                    "oi_usd":      round(oi_usd, 0),
                    "funding":     round(funding, 6),
                    "tier":        tier,
                    "direction":   direction,
                    "velocity":    "UNKNOWN",
                }
                movers.append(mover_entry)

                # Registrar no OutcomeTracker (todos os tiers)
                self._register_outcome(sym, tier, change_pct, price, direction, amount24)

                # MOVER: apenas OutcomeTracker, sem Telegram
                if tier not in _TELEGRAM_TIERS:
                    continue

                # Verificar cooldown antes de processar STRONG+
                if self._in_cooldown(sym, tier):
                    continue

                # Calcular velocidade (klines)
                velocity = self._fetch_velocity(sym)
                mover_entry["velocity"] = velocity

                # Enviar Telegram
                try:
                    from alerts import send_message
                    msg = self._format_telegram(
                        sym, tier, change_pct, price, amount24, velocity, btc_bias
                    )
                    if send_message(msg):
                        self._set_cooldown(sym, tier)
                        alerts_sent += 1
                except Exception as tg_err:
                    log.warning(f"[Movers] Telegram {sym}: {tg_err}")

                # Deep Dive para PARABOLIC
                if tier == "PARABOLIC":
                    try:
                        from trinity.modules import get_deep_dive_trigger
                        ctx = {
                            "change_pct":  change_pct,
                            "volume_usdt": amount24,
                            "oi_usd":      oi_usd,
                            "funding":     funding,
                            "velocity":    velocity,
                            "btc_bias":    btc_bias,
                            "source":      "market_movers",
                        }
                        get_deep_dive_trigger().analyze(sym, direction, ctx)
                    except Exception as dd_err:
                        log.debug(f"[Movers] DeepDive {sym}: {dd_err}")

                # Crash watchlist: PARABOLIC/EXTREME em alta → observar reversão
                if direction == "UP" and tier in ("PARABOLIC", "EXTREME"):
                    crash_watchlist.append({
                        "symbol":     sym,
                        "change_pct": change_pct,
                        "tier":       tier,
                        "added_at":   time.time(),
                    })

            except Exception as item_err:
                log.debug(f"[Movers] Erro item: {item_err}")
                continue

        # ── 4. Ordenar por variação absoluta (maiores primeiro) ───────────
        movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

        # ── 5. Atualizar estado ───────────────────────────────────────────
        elapsed = round(time.time() - t0, 1)
        with self._lock:
            self._state.update({
                "status":          "ok",
                "last_scan_ts":    time.time(),
                "last_scan_count": len(tickers),
                "elapsed_s":       elapsed,
                "alerts_sent":     alerts_sent,
                "trending":        movers[:50],
                "crash_watchlist": crash_watchlist,
                "tiers":           tier_counts,
            })

        log.info(
            f"[Movers] Varredura OK: {len(tickers)} contratos, "
            f"{len(movers)} movers (P={tier_counts['PARABOLIC']} "
            f"E={tier_counts['EXTREME']} S={tier_counts['STRONG']} "
            f"M={tier_counts['MOVER']}), "
            f"{alerts_sent} alertas Telegram, {elapsed}s"
        )
        return self.get_status()

    # ── Getters ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Retorna cópia do estado da última varredura."""
        with self._lock:
            return dict(self._state)

    def get_trending(self, limit: int = 50) -> List[dict]:
        """Retorna lista de movers da última varredura, ordenada por |change_pct|."""
        with self._lock:
            return list(self._state["trending"])[:limit]


# ── Singleton ──────────────────────────────────────────────────────────────
_instance: Optional[MarketMoversScanner] = None


def get_scanner() -> MarketMoversScanner:
    """Retorna instância singleton do MarketMoversScanner."""
    global _instance
    if _instance is None:
        _instance = MarketMoversScanner()
    return _instance
