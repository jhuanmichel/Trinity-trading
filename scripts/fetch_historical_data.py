"""
fetch_historical_data.py — Download histórico BTCUSDT 1h da Binance API pública

Uso:
    python scripts/fetch_historical_data.py          # pula se atualizado nas últimas 24h
    python scripts/fetch_historical_data.py --force  # força re-download completo

Salva em:
    data/historical/BTCUSDT_1h.parquet
    data/historical/BTCUSDT_1h_metadata.json
"""
import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

# ── Config ──────────────────────────────────────────────────────────────────
SYMBOL         = "BTCUSDT"
INTERVAL       = "1h"
START_DATE     = "2017-01-01"
BINANCE_URL    = "https://api.binance.com/api/v3/klines"
LIMIT_PER_REQ  = 1000
SLEEP_BETWEEN  = 0.3    # seg entre requests
LOG_EVERY      = 5_000  # candles entre logs de progresso
FRESH_HOURS    = 24     # horas antes de considerar dados "velhos"

ROOT_DIR  = Path(__file__).resolve().parent.parent
DATA_DIR  = ROOT_DIR / "data" / "historical"
PARQUET   = DATA_DIR / f"{SYMBOL}_{INTERVAL}.parquet"
META_FILE = DATA_DIR / f"{SYMBOL}_{INTERVAL}_metadata.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_ms(dt_str: str) -> int:
    """Converte string 'YYYY-MM-DD' para epoch ms UTC."""
    dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _load_metadata() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_metadata(meta: dict) -> None:
    META_FILE.write_text(json.dumps(meta, indent=2, default=str))


def _is_fresh() -> bool:
    """Retorna True se o parquet existe e foi atualizado há menos de FRESH_HOURS."""
    if not PARQUET.exists():
        return False
    meta = _load_metadata()
    updated_raw = meta.get("updated_at")
    if not updated_raw:
        return False
    try:
        updated = datetime.fromisoformat(updated_raw)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
        return age_h < FRESH_HOURS
    except Exception:
        return False


# ── Fetch ────────────────────────────────────────────────────────────────────

def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """
    Baixa todas as klines da Binance paginando em blocos de 1000 candles.
    Retorna lista de klines no formato Binance (lista de listas).
    """
    all_klines: list = []
    current_ms = start_ms
    logged_at  = 0

    while current_ms < end_ms:
        params = {
            "symbol":    symbol,
            "interval":  interval,
            "startTime": current_ms,
            "endTime":   end_ms,
            "limit":     LIMIT_PER_REQ,
        }
        try:
            resp = requests.get(BINANCE_URL, params=params, timeout=30)
            resp.raise_for_status()
            batch = resp.json()
        except requests.exceptions.HTTPError as e:
            log.error(f"HTTP error: {e} — aguardando 10s...")
            time.sleep(10)
            continue
        except requests.RequestException as e:
            log.error(f"Requisição falhou: {e} — aguardando 5s...")
            time.sleep(5)
            continue

        if not batch:
            break

        all_klines.extend(batch)
        count = len(all_klines)

        if count - logged_at >= LOG_EVERY:
            ts_ms  = batch[-1][0]
            dt_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            log.info(f"  {count:>7,} candles baixados — último: {dt_str}")
            logged_at = count

        # Próximo bloco começa 1ms após o fechamento da última kline
        current_ms = int(batch[-1][6]) + 1   # close_time + 1ms
        time.sleep(SLEEP_BETWEEN)

    return all_klines


def klines_to_df(klines: list) -> pd.DataFrame:
    """Converte lista de klines Binance para DataFrame OHLCV com index DatetimeIndex UTC."""
    COLS = ["timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(klines, columns=COLS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[["open", "high", "low", "close", "volume"]].sort_index()


# ── Main ─────────────────────────────────────────────────────────────────────

def run(force: bool = False) -> pd.DataFrame:
    """
    Baixa/atualiza dados históricos e salva em parquet.

    Args:
        force: se True, re-baixa do zero mesmo que os dados sejam recentes.

    Returns:
        DataFrame OHLCV completo.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not force and _is_fresh():
        meta = _load_metadata()
        log.info(
            f"Dados já atualizados ({meta.get('updated_at','?')}) — "
            f"{meta.get('total_candles',0):,} candles. Use --force para re-baixar."
        )
        return pd.read_parquet(PARQUET)

    now_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    existing = pd.DataFrame()

    # Modo incremental: se parquet existe, recomeça do último timestamp
    if PARQUET.exists() and not force:
        try:
            existing = pd.read_parquet(PARQUET)
            if not existing.empty:
                last_ts  = existing.index[-1]
                start_ms = int(last_ts.timestamp() * 1000) + 1
                log.info(
                    f"Modo incremental — continuando de {last_ts.strftime('%Y-%m-%d %H:%M')} UTC"
                )
            else:
                start_ms = _to_ms(START_DATE)
        except Exception as e:
            log.warning(f"Falha ao ler parquet existente ({e}) — baixando do zero.")
            start_ms = _to_ms(START_DATE)
    else:
        start_ms = _to_ms(START_DATE)
        log.info(f"Download completo de {START_DATE} a hoje...")

    klines = fetch_klines(SYMBOL, INTERVAL, start_ms, now_ms)

    if not klines:
        if not existing.empty:
            log.info("Nenhum candle novo — dados já estão atualizados.")
            return existing
        log.warning("Nenhum candle retornado pela API Binance.")
        return pd.DataFrame()

    new_df = klines_to_df(klines)
    log.info(
        f"Novos candles: {len(new_df):,} "
        f"({new_df.index[0].date()} → {new_df.index[-1].date()})"
    )

    # Concatena e remove duplicatas
    if not existing.empty:
        df_full = pd.concat([existing, new_df])
        df_full = df_full[~df_full.index.duplicated(keep="last")].sort_index()
    else:
        df_full = new_df

    # Salva parquet (snappy comprimido)
    df_full.to_parquet(PARQUET, engine="pyarrow", compression="snappy")

    # Salva metadata
    meta = {
        "symbol":         SYMBOL,
        "interval":       INTERVAL,
        "start_date":     str(df_full.index[0].date()),
        "end_date":       str(df_full.index[-1].date()),
        "total_candles":  len(df_full),
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    }
    _save_metadata(meta)

    log.info(f"✓ Salvo: {PARQUET}")
    log.info(f"  Total: {len(df_full):,} candles | {meta['start_date']} → {meta['end_date']}")

    return df_full


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Baixar histórico BTCUSDT 1h da Binance (pública, sem auth)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Força re-download completo mesmo que os dados estejam atualizados"
    )
    args = parser.parse_args()
    run(force=args.force)
