"""
trinity/modules/parabolic_scanner.py — RAVE-type Parabolic Move Detector

Varre todos os contratos MEXC Futures a cada 15 minutos detectando movimentos
parabólicos que precedem crashes violentos (caso RAVE +10.900%).

Critérios de trigger:
  24h > 100%              → alerta urgente + watchlist
  24h > 200%              → alerta urgente + Deep Dive automático
  7d  > 200%              → alerta + Deep Dive automático
  7d  > 500%              → alerta EXTREMO + Deep Dive prioritário

Cooldown próprio 4h/símbolo — independente dos traders de pump/crash.
"""
import asyncio
import logging
import threading
import time
from typing import Dict, Optional

log = logging.getLogger(__name__)

# ── Configuração ──────────────────────────────────────────────────────────────
ALERT_24H_THRESHOLD     = 100.0   # % 24h mínimo para qualquer alerta
DEEP_DIVE_24H_THRESHOLD = 200.0   # % 24h para ativar Deep Dive
DEEP_DIVE_7D_THRESHOLD  = 200.0   # % 7d para ativar Deep Dive
EXTREME_7D_THRESHOLD    = 500.0   # % 7d para tier EXTREMO
MIN_VOLUME_USD          = 500_000  # volume mínimo USDT 24h (filtra shitcoins sem liquidez)
COOLDOWN_HOURS          = 4        # horas de cooldown por símbolo
MAX_PER_CYCLE           = 5        # máximo de alertas por ciclo (evita flood Telegram)

# ── Estado interno ────────────────────────────────────────────────────────────
_cooldowns: Dict[str, float] = {}   # {symbol: timestamp_last_alert}


# ── Helpers de cooldown ───────────────────────────────────────────────────────

def _is_on_cooldown(symbol: str) -> bool:
    ts = _cooldowns.get(symbol)
    if ts is None:
        return False
    return (time.time() - ts) < COOLDOWN_HOURS * 3600


def _set_cooldown(symbol: str) -> None:
    _cooldowns[symbol] = time.time()


# ── Cálculo 7d ────────────────────────────────────────────────────────────────

def _calc_7d_change(mexc_symbol: str) -> Optional[float]:
    """
    Calcula variação 7d via klines diárias MEXC Futures.
    interval=Day1, limit=8 → ~8 candles diárias (7 completas + candle parcial atual)

    Fórmula: (close[-1] - open[0]) / open[0] * 100
    Retorna % ou None em caso de erro/dados insuficientes.
    """
    try:
        from trinity.traders.predictive_crash_trader.altcoin_market_scanner import _get_klines
        klines = _get_klines(mexc_symbol, interval="Day1", limit=8)
        if not klines or len(klines) < 2:
            return None
        open_7d   = float(klines[0][1])    # open da candle mais antiga (índice 0)
        close_now = float(klines[-1][4])   # close da candle mais recente
        if open_7d == 0:
            return None
        return round((close_now - open_7d) / open_7d * 100.0, 1)
    except Exception as e:
        log.debug(f"[ParabolicScanner] Erro 7d {mexc_symbol}: {e}")
        return None


# ── Alerta Telegram ───────────────────────────────────────────────────────────

def _send_parabolic_alert(symbol: str, pct_24h: float, pct_7d: Optional[float],
                          price: float, vol_usd: float, tier: str) -> None:
    """Envia alerta dedicado de parabólica via Telegram (parse_mode=HTML)."""
    try:
        from alerts import send_message

        coin = symbol.replace("USDT", "")

        if tier == "EXTREME":
            header = (
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🚨🔥 PARABÓLICA EXTREMA 🔥🚨\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            )
        elif tier == "DEEP_DIVE":
            header = "⚠️ PARABÓLICA DETECTADA"
        else:
            header = "🚀 MOVIMENTO EXTREMO"

        lines = [
            header,
            "",
            f"Ativo:       <b>{coin}</b>",
            f"Preço:       ${price:,.4g}",
            "",
            f"📊 24h:     <b>+{pct_24h:.1f}%</b>",
        ]

        if pct_7d is not None:
            lines.append(f"📅 7d:      <b>+{pct_7d:.1f}%</b>")

        lines += [
            f"💰 Volume:  ${vol_usd:,.0f}",
            "",
        ]

        if tier == "EXTREME":
            lines += [
                "⚠️ RISCO EXTREMO DE REVERSÃO",
                "Deep Dive automático em andamento...",
                "",
                "📌 Movimento >500% em 7d — alto risco pump &amp; dump.",
            ]
        elif tier == "DEEP_DIVE":
            lines += [
                "Deep Dive automático em andamento...",
                "📌 Alta probabilidade de reversão violenta.",
            ]
        else:
            lines.append("📌 Monitorar para possível short setup.")

        msg = "\n".join(lines)
        ok = send_message(msg)
        if ok:
            log.info(f"[ParabolicScanner] Alerta enviado: {symbol} +{pct_24h:.1f}% (tier={tier})")
        else:
            log.warning(f"[ParabolicScanner] Telegram recusou alerta para {symbol}")
    except Exception as e:
        log.error(f"[ParabolicScanner] Erro ao enviar alerta {symbol}: {e}")


# ── Deep Dive ─────────────────────────────────────────────────────────────────

def _trigger_deep_dive(symbol: str, pct_24h: float, pct_7d: Optional[float],
                       price: float, vol_usd: float, funding: float) -> None:
    """Dispara análise Deep Dive em thread daemon (não bloqueia o scheduler)."""
    try:
        from trinity.modules import get_deep_dive_trigger

        trigger = get_deep_dive_trigger()
        context = {
            "score":             85,           # score sintético — parabólica sempre é alto risco
            "overextension_pct": pct_24h,
            "price_change_pct":  pct_24h,
            "price_current":     price,
            "volume_24h":        vol_usd,
            "funding_rate":      funding,
            "oi_change_pct":     0,
            "change_7d_pct":     pct_7d,
            "is_parabolic":      True,
            "klines_4h":         [],
            "klines_1h":         [],
        }

        def _run() -> None:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    trigger.analyze(symbol, "CRASH", context)
                )
                loop.close()
                if result:
                    log.info(f"[ParabolicScanner] Deep Dive concluído: {symbol}")
            except Exception as exc:
                log.error(f"[ParabolicScanner] Deep Dive erro {symbol}: {exc}")

        t = threading.Thread(target=_run, daemon=True, name=f"deepdive-{symbol}")
        t.start()
        log.info(f"[ParabolicScanner] Deep Dive disparado: {symbol} +{pct_24h:.1f}%")

    except Exception as e:
        log.error(f"[ParabolicScanner] Erro ao disparar Deep Dive {symbol}: {e}")


# ── Ponto de entrada principal ────────────────────────────────────────────────

def run_parabolic_scan() -> None:
    """
    Chamado pelo scheduler a cada 15 minutos.
    Varre todos os tickers MEXC Futures e detecta movimentos parabólicos.
    """
    try:
        from trinity.traders.predictive_crash_trader.altcoin_market_scanner import (
            _fetch_universe,
            _from_mexc,
            _to_mexc,
        )

        log.info("[ParabolicScanner] Iniciando varredura...")
        tickers = _fetch_universe()
        if not tickers:
            log.warning("[ParabolicScanner] Universo vazio — abortando scan")
            return

        # ── Filtragem inicial (rápida — sem chamadas extras de API) ──────────
        candidates = []
        for t in tickers:
            try:
                symbol  = _from_mexc(str(t.get("symbol", "")))
                rf      = float(t.get("riseFallRate", 0))
                pct_24h = rf * 100.0
                vol_usd = float(t.get("amount24", 0))
                price   = float(t.get("lastPrice", 0))
                funding = float(t.get("fundingRate", 0))

                if pct_24h < ALERT_24H_THRESHOLD:
                    continue
                if vol_usd < MIN_VOLUME_USD:
                    continue
                if _is_on_cooldown(symbol):
                    continue

                candidates.append({
                    "symbol":   symbol,
                    "pct_24h":  pct_24h,
                    "vol_usd":  vol_usd,
                    "price":    price,
                    "funding":  funding,
                })
            except (ValueError, TypeError):
                continue

        if not candidates:
            log.debug("[ParabolicScanner] Nenhuma parabólica detectada neste ciclo")
            return

        # Ordena por variação 24h (maiores primeiro)
        candidates.sort(key=lambda x: x["pct_24h"], reverse=True)
        log.info(f"[ParabolicScanner] {len(candidates)} candidatos — processando até {MAX_PER_CYCLE}")

        # ── Processamento por candidato ───────────────────────────────────────
        processed = 0
        for c in candidates[:MAX_PER_CYCLE]:
            symbol  = c["symbol"]
            pct_24h = c["pct_24h"]
            vol_usd = c["vol_usd"]
            price   = c["price"]
            funding = c["funding"]

            # Busca variação 7d (1 chamada de API por candidato)
            mexc_sym = _to_mexc(symbol)
            pct_7d   = _calc_7d_change(mexc_sym)

            # ── Determina tier ────────────────────────────────────────────────
            needs_deep_dive = False
            tier = "ALERT"

            if pct_7d is not None and pct_7d >= EXTREME_7D_THRESHOLD:
                tier = "EXTREME"
                needs_deep_dive = True
            elif pct_24h >= DEEP_DIVE_24H_THRESHOLD:
                tier = "DEEP_DIVE"
                needs_deep_dive = True
            elif pct_7d is not None and pct_7d >= DEEP_DIVE_7D_THRESHOLD:
                tier = "DEEP_DIVE"
                needs_deep_dive = True

            pct_7d_str = f"{pct_7d:.1f}%" if pct_7d is not None else "N/A"
            log.info(
                f"[ParabolicScanner] {symbol} | 24h={pct_24h:.1f}% | "
                f"7d={pct_7d_str} | vol=${vol_usd:,.0f} | tier={tier}"
            )

            _send_parabolic_alert(symbol, pct_24h, pct_7d, price, vol_usd, tier)
            _set_cooldown(symbol)

            if needs_deep_dive:
                _trigger_deep_dive(symbol, pct_24h, pct_7d, price, vol_usd, funding)

            processed += 1
            time.sleep(1)   # pausa entre alertas — evita flood Telegram

        log.info(f"[ParabolicScanner] Ciclo concluído — {processed} alertas enviados")

    except Exception as e:
        log.error(f"[ParabolicScanner] Erro no scan: {e}", exc_info=True)
