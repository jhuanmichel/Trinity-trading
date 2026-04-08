"""
backtester/trade_simulator.py — Walk-forward + detecção de exits

Percorre os candles 15m em passos de 4h (step_bars=16).
Em cada passo gera sinal via BacktestSME. Se sinal válido, varre
os próximos max_forward candles para detectar TP1/TP2/TP3 ou Stop.

Exit rule: 1/3 no TP1, 1/3 no TP2, 1/3 no TP3 — ou Stop fecha 100%.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class PartialExit:
    tp:       str     # "TP1" | "TP2" | "TP3" | "STOP" | "TIMEOUT"
    price:    float
    fraction: float   # porção da posição fechada (0.333...)
    pnl_r:    float   # retorno em R desta parcela


@dataclass
class Trade:
    id:            int
    timestamp:     pd.Timestamp
    direction:     str            # "LONG" | "SHORT"
    smc_score:     float
    confluences:   int
    alignment:     str
    entry:         float
    stop:          float
    tp1:           Optional[float]
    tp2:           Optional[float]
    tp3:           Optional[float]
    rr1:           Optional[float]
    rr2:           Optional[float]
    rr3:           Optional[float]
    partial_exits: list = field(default_factory=list)
    exit_reason:   str  = "TIMEOUT"
    exit_price:    float = 0.0
    pnl_r:         float = 0.0
    pnl_pct:       float = 0.0    # % do capital na trade (com 1% risco)
    duration_bars: int   = 0
    result:        str   = "TIMEOUT"


def _check_exits(df_forward: pd.DataFrame, sig: dict) -> tuple[list[PartialExit], str, float, int]:
    """
    Varre df_forward para detectar exits parciais.

    Retorna:
        (partial_exits, exit_reason, exit_price, duration_bars)
    """
    entry     = sig["entry"]
    stop      = sig["stop"]
    tp1       = sig.get("tp1")
    tp2       = sig.get("tp2")
    tp3       = sig.get("tp3")
    direction = sig["direction"]

    risk = abs(entry - stop) if stop else 0.0
    if risk == 0:
        return [], "NO_RISK", entry, 0

    def r(price: float) -> float:
        """Retorno em R para um preço de saída."""
        diff = (price - entry) if direction == "LONG" else (entry - price)
        return round(diff / risk, 3)

    partial_exits = []
    hit_tp1 = hit_tp2 = hit_tp3 = False
    remaining = 1.0
    last_price = entry

    for bar_idx, (ts, row) in enumerate(df_forward.iterrows()):
        high_  = float(row["high"])
        low_   = float(row["low"])
        close_ = float(row["close"])
        last_price = close_

        if direction == "LONG":
            # TP hits (high toca ou ultrapassa o nível)
            if not hit_tp1 and tp1 and high_ >= tp1:
                frac = round(1/3, 6)
                partial_exits.append(PartialExit("TP1", tp1, frac, r(tp1)))
                hit_tp1   = True
                remaining -= frac

            if not hit_tp2 and tp2 and high_ >= tp2:
                frac = round(1/3, 6)
                partial_exits.append(PartialExit("TP2", tp2, frac, r(tp2)))
                hit_tp2   = True
                remaining -= frac

            if not hit_tp3 and tp3 and high_ >= tp3:
                frac = remaining
                partial_exits.append(PartialExit("TP3", tp3, frac, r(tp3)))
                hit_tp3   = True
                remaining  = 0.0
                return partial_exits, "TP3", tp3, bar_idx + 1

            # Stop hit (close abaixo do stop)
            if stop and low_ <= stop:
                if remaining > 0:
                    partial_exits.append(PartialExit("STOP", stop, remaining, r(stop)))
                    remaining = 0.0
                return partial_exits, "STOP", stop, bar_idx + 1

        elif direction == "SHORT":
            if not hit_tp1 and tp1 and low_ <= tp1:
                frac = round(1/3, 6)
                partial_exits.append(PartialExit("TP1", tp1, frac, r(tp1)))
                hit_tp1   = True
                remaining -= frac

            if not hit_tp2 and tp2 and low_ <= tp2:
                frac = round(1/3, 6)
                partial_exits.append(PartialExit("TP2", tp2, frac, r(tp2)))
                hit_tp2   = True
                remaining -= frac

            if not hit_tp3 and tp3 and low_ <= tp3:
                frac = remaining
                partial_exits.append(PartialExit("TP3", tp3, frac, r(tp3)))
                hit_tp3   = True
                remaining  = 0.0
                return partial_exits, "TP3", tp3, bar_idx + 1

            if stop and high_ >= stop:
                if remaining > 0:
                    partial_exits.append(PartialExit("STOP", stop, remaining, r(stop)))
                    remaining = 0.0
                return partial_exits, "STOP", stop, bar_idx + 1

    # Timeout — fecha restante ao último preço
    if remaining > 0:
        partial_exits.append(PartialExit("TIMEOUT", last_price, remaining, r(last_price)))

    # Determina exit_reason pelo TP mais alto atingido
    hits = [pe.tp for pe in partial_exits if pe.tp not in ("STOP", "TIMEOUT")]
    if   "TP2" in hits: reason = "TP2"
    elif "TP1" in hits: reason = "TP1"
    elif "STOP" in hits: reason = "STOP"
    else:                reason = "TIMEOUT"

    exit_p = partial_exits[-1].price if partial_exits else entry
    return partial_exits, reason, exit_p, len(df_forward)


def _net_pnl_r(partial_exits: list[PartialExit]) -> float:
    """Soma ponderada de R de todas as saídas parciais."""
    return round(sum(pe.pnl_r * pe.fraction for pe in partial_exits), 3)


def run_walkforward(
    dfs:             dict,
    symbol:          str   = "BTC/USDT:USDT",
    step_bars:       int   = 16,    # 16 × 15m = 4h
    lookback:        int   = 300,   # janela de análise
    max_forward:     int   = 96,    # máximo 96 × 15m = 24h para atingir exit
    threshold:       float = 60.0,  # convicção mínima (LONG: score >= threshold; SHORT: score <= 100-threshold)
    min_confluences: int   = 5,     # mínimo de camadas alinhadas (de 8)
) -> list[Trade]:
    """
    Percorre os candles 15m com passo step_bars, gera sinais e simula exits.

    Filtros de qualidade:
      - LONG:  smc_score >= threshold  (ex: >= 60)
      - SHORT: smc_score <= 100-threshold (ex: <= 40)
      - confluences >= min_confluences
      - stop coerente com direção
    """
    from backtester.signal_generator import generate_signal_at

    df_15m = dfs.get("15m", pd.DataFrame())
    if df_15m.empty:
        log.error("[run_walkforward] df_15m vazio")
        return []

    n       = len(df_15m)
    trades  = []
    trade_id = 0
    last_trade_idx = -1   # Evita trades sobrepostos

    indices = range(lookback, n - max_forward, step_bars)
    log.info(
        f"[run_walkforward] {len(indices)} passos · {len(df_15m)} candles 15m "
        f"| threshold={threshold} | min_confluences={min_confluences}"
    )

    for i in indices:
        # Evita trade sobrepostos (aguarda exit do anterior)
        if trade_id > 0 and i < last_trade_idx:
            continue

        current_ts = df_15m.index[i]
        window     = df_15m.iloc[i - lookback: i + 1].copy()

        sig = generate_signal_at(window, symbol, dfs, current_ts)

        if sig is None:
            continue

        # Filtro de convicção — simétrico para LONG e SHORT
        smc_score = sig["smc_score"]
        direction = sig.get("direction", "")
        if direction == "LONG"  and smc_score <  threshold:
            continue
        if direction == "SHORT" and smc_score > (100.0 - threshold):
            continue

        # Filtro de confluências
        if sig.get("confluences", 0) < min_confluences:
            continue

        if not sig.get("entry") or not sig.get("stop"):
            continue

        # Verifica se os TPs são válidos (não None e coerentes com direção)
        direction = sig["direction"]
        entry = sig["entry"]
        stop  = sig["stop"]
        tp1   = sig.get("tp1")
        tp2   = sig.get("tp2")
        tp3   = sig.get("tp3")

        if not tp1:
            continue

        # Valida consistência do sinal (stop deve ser oposto à entry por direção)
        if direction == "LONG"  and stop >= entry:
            continue
        if direction == "SHORT" and stop <= entry:
            continue

        # Candles forward para detectar exit
        df_fwd = df_15m.iloc[i + 1: i + 1 + max_forward].copy()
        if df_fwd.empty:
            continue

        partial_exits, exit_reason, exit_price, dur = _check_exits(df_fwd, sig)
        pnl_r = _net_pnl_r(partial_exits)

        # P&L% do capital com 1% de risco por trade
        # risco = 1% do capital → ganho/perda = pnl_r × 1%
        pnl_pct = round(pnl_r * 1.0, 4)  # 1.0 = 1% de risco

        result = "WIN" if pnl_r > 0 else "LOSS" if pnl_r < 0 else "BREAK"

        trade_id += 1
        last_trade_idx = i + dur

        trade = Trade(
            id            = trade_id,
            timestamp     = current_ts,
            direction     = direction,
            smc_score     = sig["smc_score"],
            confluences   = sig["confluences"],
            alignment     = sig["alignment"],
            entry         = entry,
            stop          = stop,
            tp1           = tp1,
            tp2           = tp2,
            tp3           = tp3,
            rr1           = sig.get("rr1"),
            rr2           = sig.get("rr2"),
            rr3           = sig.get("rr3"),
            partial_exits = partial_exits,
            exit_reason   = exit_reason,
            exit_price    = exit_price,
            pnl_r         = pnl_r,
            pnl_pct       = pnl_pct,
            duration_bars = dur,
            result        = result,
        )
        trades.append(trade)
        log.info(
            f"[trade #{trade_id}] {current_ts.date()} {direction} "
            f"score={sig['smc_score']:.1f} → {exit_reason} "
            f"R={pnl_r:+.2f} ({result})"
        )

    log.info(f"[run_walkforward] Total trades gerados: {len(trades)}")
    return trades
