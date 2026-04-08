"""
altcoin_scanner.py — Scanner SMC para top altcoins

Pipeline (a cada 5 min):
  1. Busca candles 15m via MEXC REST public API (sem autenticação)
  2. Roda SmartMoneyEngine.detect_market_structure() + OB + FVG
  3. Calcula score e direção para cada coin
  4. Retorna top N ordenados por convicção (|score - 50|)

Sem MTF (somente 15m) para manter velocidade no scan de múltiplos ativos.
"""
import logging
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import pandas as pd

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# ── Coins a escanear (large + mid cap com liquidez em MEXC) ──────────────────
SCAN_SYMBOLS = [
    "ETHUSDT",   # Ethereum
    "SOLUSDT",   # Solana
    "BNBUSDT",   # BNB
    "XRPUSDT",   # XRP
    "ADAUSDT",   # Cardano
    "DOGEUSDT",  # Dogecoin
    "AVAXUSDT",  # Avalanche
    "LINKUSDT",  # Chainlink
    "DOTUSDT",   # Polkadot
    "NEARUSDT",  # NEAR
    "UNIUSDT",   # Uniswap
    "APTUSDT",   # Aptos
    "INJUSDT",   # Injective
    "SUIUSDT",   # Sui
    "ARBUSDT",   # Arbitrum
    "OPUSDT",    # Optimism
    "ATOMUSDT",  # Cosmos
    "LDOUSDT",   # Lido
    "FETUSDT",   # Fetch.ai
    "SHIBUSDT",  # Shiba Inu
    "PEPEUSDT",  # Pepe
    "RUNEUSDT",  # THORChain
    "FTMUSDT",   # Fantom
    "WLDUSDT",   # Worldcoin
    "GRTUSDT",   # The Graph
    "ZECUSDT",   # Zcash
]

TOP_RESULTS    = 8     # top N no resultado
MEXC_KLINES    = "https://api.mexc.com/api/v3/klines"
REQUEST_DELAY  = 0.2   # segundos entre requests


# ── Fetch OHLCV via MEXC REST ────────────────────────────────────────────────

def _fetch_klines(symbol: str, interval: str = "15m", limit: int = 200) -> Optional[pd.DataFrame]:
    """Busca candles via MEXC public REST API. Retorna DataFrame ou None."""
    try:
        resp = requests.get(
            MEXC_KLINES,
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=5,
        )
        resp.raise_for_status()
        raw = resp.json()
        if not raw or not isinstance(raw, list):
            return None

        df = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df = df[["open", "high", "low", "close", "volume"]]
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df

    except Exception as e:
        log.debug(f"[altcoin_scanner] fetch {symbol}: {e}")
        return None


# ── Análise SMC rápida (só 15m) ──────────────────────────────────────────────

def _quick_smc(df: pd.DataFrame, symbol: str) -> Optional[dict]:
    """Roda SMC engine no 15m e retorna dict com score/direção."""
    try:
        from smart_money_engine import SmartMoneyEngine
        engine = SmartMoneyEngine(df, symbol)
        ms  = engine.detect_market_structure()
        ob  = engine.detect_order_blocks()
        fvg = engine.detect_fvg()

        score     = ms.get("score", 50)
        bias      = ms.get("bias", "NEUTRO")
        structure = ms.get("structure", "?")

        # Direção baseada no score e bias
        if   score >= 60 and bias == "BULLISH": direction = "LONG"
        elif score <= 40 and bias == "BEARISH": direction = "SHORT"
        else:                                    direction = "NEUTRO"

        # Sinal completo (entry/stop/TP)
        sig = engine.analyze().get("signal", {})
        entry = sig.get("entry") if sig.get("valid") else None
        stop  = sig.get("stop")  if sig.get("valid") else None
        tp1   = sig.get("tp1")   if sig.get("valid") else None
        tp2   = sig.get("tp2")   if sig.get("valid") else None

        # Preço atual
        price = float(df["close"].iloc[-1])

        # Variação 24h estimada (última vs 96 candles atrás em 15m)
        if len(df) >= 96:
            price_24h_ago = float(df["close"].iloc[-96])
            change_24h    = round((price - price_24h_ago) / price_24h_ago * 100, 2)
        else:
            change_24h = 0.0

        return {
            "symbol":     symbol.replace("USDT", ""),
            "pair":       f"{symbol.replace('USDT', '')}/USDT",
            "price":      price,
            "change_24h": change_24h,
            "smc_score":  round(score, 1),
            "direction":  direction,
            "bias":       bias,
            "structure":  structure,
            "bos_bull":   ms.get("bos_bull", False),
            "bos_bear":   ms.get("bos_bear", False),
            "choch":      ms.get("choch", False),
            "ob_count":   len([o for o in ob if o.get("valid", False)]),
            "fvg_count":  len(fvg.get("bull_fvgs", [])) + len(fvg.get("bear_fvgs", [])),
            "entry":      entry,
            "stop":       stop,
            "tp1":        tp1,
            "tp2":        tp2,
            "conviction": round(abs(score - 50), 1),  # distância do neutro
        }
    except Exception as e:
        log.debug(f"[altcoin_scanner] SMC {symbol}: {e}")
        return None


# ── Scanner principal ────────────────────────────────────────────────────────

def run_altcoin_scan() -> dict:
    """
    Escaneia SCAN_SYMBOLS com SMC engine e retorna top N por convicção.

    Retorna dict com:
        scan_ts      — ISO timestamp
        coins_scanned — total processado
        candidates   — lista ordenada por convicção
    """
    log.info(f"[altcoin_scanner] Iniciando scan de {len(SCAN_SYMBOLS)} coins ...")
    candidates = []

    for sym in SCAN_SYMBOLS:
        df = _fetch_klines(sym, interval="15m", limit=250)
        if df is None or len(df) < 50:
            log.debug(f"[altcoin_scanner] {sym}: dados insuficientes")
            time.sleep(REQUEST_DELAY)
            continue

        result = _quick_smc(df, sym)
        if result:
            candidates.append(result)
            log.debug(
                f"[altcoin_scanner] {sym}: score={result['smc_score']} "
                f"dir={result['direction']} conv={result['conviction']}"
            )
        time.sleep(REQUEST_DELAY)

    # Ordena: primeiro LONG/SHORT por convicção, depois NEUTRO
    def sort_key(c):
        base = c["conviction"]
        # prioriza sinais com entrada definida
        if c["direction"] != "NEUTRO" and c.get("entry"):
            base += 5
        return base

    candidates.sort(key=sort_key, reverse=True)

    result = {
        "scan_ts":      datetime.now(timezone.utc).isoformat(),
        "coins_scanned": len(candidates),
        "candidates":   candidates[:TOP_RESULTS],
    }
    log.info(
        f"[altcoin_scanner] Concluído: {len(candidates)} coins | "
        f"top={[c['symbol'] for c in candidates[:TOP_RESULTS]]}"
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json
    r = run_altcoin_scan()
    print(json.dumps(r, indent=2, default=str))
