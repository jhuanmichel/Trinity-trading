"""
manipulation_detector.py — Detecta movimentos manipulados e trava sinais.

Analisa JANELA de 3 candles (não apenas a última) para detectar:
  1. Pump & Dump: range extremo + volume spike + reversão rápida
  2. Flash Crash: queda vertical com volume anômalo
  3. Stop Hunt: wick gigante com body mínimo (preço volta ao lugar)
  4. Wash Trading: volume extremo mas preço mal se move
  5. Multi-candle P&D: pump em c1 seguido de dump em c3

Lock de 15 minutos. Fail-open: se o detector falhar, candidato passa normalmente.
"""
import logging
import time
from typing import Dict

log = logging.getLogger(__name__)

_manipulation_locks: Dict[str, dict] = {}
LOCK_DURATION_S = 900  # 15 minutos


def check_manipulation(symbol: str, coin_data: dict) -> dict:
    """
    Verifica manipulação em janela de 3 candles.

    Args:
        symbol:    ticker ex. "SOLUSDT"
        coin_data: dict com "price" e "klines_15m" (lista de candles OHLCV)

    Returns:
        {"locked": bool, "reason": str, "expires": float}
    """
    now = time.time()

    # Verificar lock existente ainda ativo
    lock = _manipulation_locks.get(symbol)
    if lock and (now - lock["ts"]) < LOCK_DURATION_S:
        return {
            "locked": True,
            "reason": lock["reason"],
            "expires": lock["ts"] + LOCK_DURATION_S,
        }

    # Limpar lock expirado
    if lock:
        del _manipulation_locks[symbol]

    klines = coin_data.get("klines_15m", [])
    if not klines or len(klines) < 5:
        return _no_lock()

    try:
        price = float(coin_data.get("price", 0))
        if price <= 0:
            return _no_lock()

        # ── Parsear janela de 3 últimas velas ─────────────────────────────
        window   = klines[-3:]
        candles  = []
        for k in window:
            try:
                candles.append({
                    "o": float(k[1]), "h": float(k[2]),
                    "l": float(k[3]), "c": float(k[4]),
                    "v": float(k[5]),
                })
            except (IndexError, ValueError, TypeError):
                continue

        if len(candles) < 3:
            return _no_lock()

        # ── Volume médio das 10 velas anteriores ─────────────────────────
        prior_vols = []
        for k in klines[-13:-3]:
            try:
                prior_vols.append(float(k[5]))
            except (IndexError, ValueError, TypeError):
                continue
        avg_vol = sum(prior_vols) / len(prior_vols) if prior_vols else 1.0

        last        = candles[-1]
        body        = abs(last["c"] - last["o"])
        upper_wick  = last["h"] - max(last["c"], last["o"])
        lower_wick  = min(last["c"], last["o"]) - last["l"]
        total_range = last["h"] - last["l"]
        range_pct   = (total_range / last["o"] * 100) if last["o"] > 0 else 0
        vol_spike   = last["v"] / avg_vol if avg_vol > 0 else 1.0
        wick_ratio  = (upper_wick + lower_wick) / total_range if total_range > 0 else 0

        # ── Padrão 1: Pump & Dump (1 candle) ──────────────────────────────
        if range_pct > 6.0 and vol_spike > 8.0 and wick_ratio > 0.60:
            return _set_lock(symbol, now,
                f"Pump&Dump: range {range_pct:.1f}% vol {vol_spike:.0f}x wick {wick_ratio:.0%}")

        # ── Padrão 2: Multi-candle pump+dump ──────────────────────────────
        c1, c2, c3 = candles
        c1_move = (c1["c"] - c1["o"]) / c1["o"] * 100 if c1["o"] > 0 else 0
        c3_move = (c3["c"] - c3["o"]) / c3["o"] * 100 if c3["o"] > 0 else 0

        if c1_move > 4.0 and c3_move < -3.0:
            total_vol_3 = c1["v"] + c2["v"] + c3["v"]
            if avg_vol > 0 and total_vol_3 > avg_vol * 15:
                return _set_lock(symbol, now,
                    f"Multi-candle P&D: +{c1_move:.1f}% → {c3_move:.1f}% vol {total_vol_3/avg_vol:.0f}x")

        # ── Padrão 3: Flash crash ──────────────────────────────────────────
        if range_pct > 10.0 and vol_spike > 5.0:
            return _set_lock(symbol, now,
                f"Flash crash: range {range_pct:.1f}% vol {vol_spike:.0f}x")

        # ── Padrão 4: Stop hunt ────────────────────────────────────────────
        max_wick = max(upper_wick, lower_wick)
        body_pct = (body / last["o"] * 100) if last["o"] > 0 else 0
        wick_pct = (max_wick / last["o"] * 100) if last["o"] > 0 else 0

        if wick_pct > 3.5 and body_pct < 0.5 and vol_spike > 3.0:
            return _set_lock(symbol, now,
                f"Stop hunt: wick {wick_pct:.1f}% body {body_pct:.2f}% vol {vol_spike:.0f}x")

        # ── Padrão 5: Wash trading ─────────────────────────────────────────
        if vol_spike > 12.0 and range_pct < 1.0:
            return _set_lock(symbol, now,
                f"Wash trading: vol {vol_spike:.0f}x mas range {range_pct:.1f}%")

    except Exception as e:
        log.debug(f"[ManipDetect] Erro {symbol}: {e}")
        # fail-open — qualquer erro libera o candidato

    return _no_lock()


def _set_lock(symbol: str, now: float, reason: str) -> dict:
    _manipulation_locks[symbol] = {"ts": now, "reason": reason}
    log.warning(f"[ManipDetect] 🚫 {symbol} LOCKED 15min: {reason}")
    return {"locked": True, "reason": reason, "expires": now + LOCK_DURATION_S}


def _no_lock() -> dict:
    return {"locked": False, "reason": "", "expires": 0}


def clear_expired_locks():
    """Remove locks expirados. Seguro para chamar a qualquer momento."""
    now     = time.time()
    expired = [s for s, l in _manipulation_locks.items()
               if (now - l["ts"]) >= LOCK_DURATION_S]
    for s in expired:
        del _manipulation_locks[s]
    if expired:
        log.debug(f"[ManipDetect] {len(expired)} locks expirados removidos")
