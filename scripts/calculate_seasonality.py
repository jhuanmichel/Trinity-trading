"""
scripts/calculate_seasonality.py — Coleta 3 anos de OHLCV BTC/USDT da Binance
e calcula multiplicadores sazonais por dia da semana e hora UTC.

Execução: python scripts/calculate_seasonality.py
Saída:    dashboard/seasonality_data.json

Standalone — não importa nenhum módulo do projeto principal.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ── Configuração ──────────────────────────────────────────────────────────────
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
SYMBOL         = "BTCUSDT"
YEARS          = 3
LIMIT_PER_REQ  = 1000
SLEEP_BETWEEN  = 0.3   # segundos entre requests (rate-limit amigável)
OUTPUT_FILE    = Path("dashboard/seasonality_data.json")
FLOOR          = 0.75
CEILING        = 1.25
LOG_EVERY      = 500   # loga progresso a cada N candles

DAY_LABELS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]


def _fetch_1h_candles(start_ms: int, end_ms: int) -> list:
    """Busca todos os candles 1h da Binance entre start_ms e end_ms."""
    all_candles: list = []
    current_ms  = start_ms
    # Estimativa: cada candle de 1h = 3.600.000 ms
    total_expected = max(1, (end_ms - start_ms) // 3_600_000)
    print(f"[SEASONALITY] Estimativa: ~{total_expected} candles de 1h a coletar")

    while current_ms < end_ms:
        try:
            resp = requests.get(
                BINANCE_KLINES,
                params={
                    "symbol":    SYMBOL,
                    "interval":  "1h",
                    "startTime": current_ms,
                    "limit":     LIMIT_PER_REQ,
                },
                timeout=15,
            )
            resp.raise_for_status()
            candles = resp.json()
            if not candles:
                break

            all_candles.extend(candles)
            n = len(all_candles)

            # Progresso a cada LOG_EVERY candles
            if n % LOG_EVERY < LIMIT_PER_REQ:
                pct = round(n / total_expected * 100)
                print(f"[SEASONALITY] {n}/{total_expected} candles coletados ({pct}%)")

            # Avança startTime para o próximo batch
            last_ts    = int(candles[-1][0])
            current_ms = last_ts + 3_600_000   # +1h em ms

            if len(candles) < LIMIT_PER_REQ:
                break   # chegou ao fim dos dados disponíveis

            time.sleep(SLEEP_BETWEEN)

        except requests.exceptions.RequestException as e:
            print(f"[SEASONALITY] Erro de rede: {e} — aguardando 5s")
            time.sleep(5)
        except Exception as e:
            print(f"[SEASONALITY] Erro inesperado: {e} — abortando batch")
            break

    print(f"[SEASONALITY] Total coletado: {len(all_candles)} candles de 1h")
    return all_candles


def _normalize(value: float, min_val: float, max_val: float) -> float:
    """Normaliza para range [FLOOR, CEILING]. Retorna 1.0 se sem variação."""
    if max_val == min_val:
        return 1.0
    raw = FLOOR + (value - min_val) / (max_val - min_val) * (CEILING - FLOOR)
    return round(max(FLOOR, min(CEILING, raw)), 4)


def _confidence(count: int) -> str:
    if count >= 100: return "high"
    if count >= 50:  return "medium"
    return "low"


def main() -> None:
    now      = datetime.now(timezone.utc)
    end_ms   = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=YEARS * 365)).timestamp() * 1000)

    period_start = datetime.utcfromtimestamp(start_ms / 1000).strftime("%Y-%m-%d")
    period_end   = datetime.utcfromtimestamp(end_ms   / 1000).strftime("%Y-%m-%d")

    print(f"[SEASONALITY] Coletando {YEARS} anos de {SYMBOL} 1h: {period_start} → {period_end}")
    candles = _fetch_1h_candles(start_ms, end_ms)

    if not candles:
        print("[SEASONALITY] ERRO: Nenhum candle coletado — abortando")
        sys.exit(1)

    # ── Agrega retornos por dia_semana e hora_utc ─────────────────────────────
    day_returns:  dict[int, list[float]] = {i: [] for i in range(7)}
    hour_returns: dict[int, list[float]] = {i: [] for i in range(24)}

    for c in candles:
        try:
            ts_ms  = int(c[0])
            open_p = float(c[1])
            close  = float(c[4])
            if open_p <= 0:
                continue
            ret_pct = (close - open_p) / open_p * 100
            dt = datetime.utcfromtimestamp(ts_ms / 1000)
            day_returns[dt.weekday()].append(ret_pct)
            hour_returns[dt.hour].append(ret_pct)
        except Exception:
            continue

    # Médias por bucket
    day_avgs  = {d: (sum(v) / len(v) if v else 0.0) for d, v in day_returns.items()}
    hour_avgs = {h: (sum(v) / len(v) if v else 0.0) for h, v in hour_returns.items()}

    # Normalização
    d_vals = list(day_avgs.values())
    h_vals = list(hour_avgs.values())
    d_min, d_max = min(d_vals), max(d_vals)
    h_min, h_max = min(h_vals), max(h_vals)

    by_day: dict = {}
    for d in range(7):
        avg   = day_avgs[d]
        count = len(day_returns[d])
        by_day[str(d)] = {
            "label":          DAY_LABELS[d],
            "avg_return_pct": round(avg, 4),
            "multiplier":     _normalize(avg, d_min, d_max),
            "sample_count":   count,
            "confidence":     _confidence(count),
        }

    by_hour: dict = {}
    for h in range(24):
        avg   = hour_avgs[h]
        count = len(hour_returns[h])
        by_hour[str(h)] = {
            "avg_return_pct": round(avg, 4),
            "multiplier":     _normalize(avg, h_min, h_max),
            "sample_count":   count,
        }

    # Top 5 melhores e 5 piores combinações dia×hora
    combos: list = []
    for d in range(7):
        dm = by_day[str(d)]["multiplier"]
        dl = by_day[str(d)]["label"]
        for h in range(24):
            hm       = by_hour[str(h)]["multiplier"]
            combined = round(max(FLOOR, min(CEILING, dm * hm)), 4)
            combos.append({"day": d, "hour": h, "label": f"{dl} {h}h UTC", "combined": combined})

    combos.sort(key=lambda x: x["combined"], reverse=True)
    top_combinations = combos[:5] + combos[-5:]

    # ── Salva seasonality_data.json ───────────────────────────────────────────
    data = {
        "generated_at":           now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period_start":           period_start,
        "period_end":             period_end,
        "total_candles_analyzed": len(candles),
        "by_day_of_week":         by_day,
        "by_hour_utc":            by_hour,
        "top_combinations":       top_combinations,
        "metadata": {"floor": FLOOR, "ceiling": CEILING, "neutral": 1.0},
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # ── Resumo legível ────────────────────────────────────────────────────────
    best_d  = max(by_day.items(),  key=lambda x: x[1]["avg_return_pct"])
    worst_d = min(by_day.items(),  key=lambda x: x[1]["avg_return_pct"])
    best_h  = max(by_hour.items(), key=lambda x: x[1]["avg_return_pct"])
    worst_h = min(by_hour.items(), key=lambda x: x[1]["avg_return_pct"])

    print(
        f"\n[SEASONALITY] Análise concluída.\n"
        f"   Melhor dia:     {best_d[1]['label']} ({best_d[1]['avg_return_pct']:+.4f}% médio, "
        f"multiplicador {best_d[1]['multiplier']})\n"
        f"   Pior dia:       {worst_d[1]['label']} ({worst_d[1]['avg_return_pct']:+.4f}% médio, "
        f"multiplicador {worst_d[1]['multiplier']})\n"
        f"   Melhor horário: {best_h[0]}h UTC ({best_h[1]['avg_return_pct']:+.4f}%, "
        f"multiplicador {best_h[1]['multiplier']})\n"
        f"   Pior horário:   {worst_h[0]}h UTC ({worst_h[1]['avg_return_pct']:+.4f}%, "
        f"multiplicador {worst_h[1]['multiplier']})\n"
        f"   Arquivo salvo:  {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
