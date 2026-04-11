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

import json
import requests
import pandas as pd

log = logging.getLogger(__name__)

BASE_DIR   = Path(__file__).parent.parent.parent.parent
STATE_FILE = BASE_DIR / "dashboard" / "current_state.json"
LOGS_DIR   = BASE_DIR / "logs"
sys.path.insert(0, str(BASE_DIR))

# ── Thresholds de score em 3 camadas (C2) ────────────────────────────────────
SCORE_THRESHOLDS = {
    "display":         45,   # abaixo: ocultar card no dashboard
    "actionable":      55,   # abaixo: não salvar como sinal, só observação
    "telegram":        62,   # abaixo: nunca enviar alerta Telegram
    "high_conviction": 70,   # acima: alerta prioritário
}

# ── Order Block limites (C4) ──────────────────────────────────────────────────
OB_MAX_COUNT    = 4    # máximo por direção a exibir
OB_MAX_AGE_BARS = 50   # OBs mais antigos que 50 candles são descartados

# ── Confirmação estrutural necessária por direção (C1) ────────────────────────
# Campos reais de detect_market_structure(): bos_bull, bos_bear, choch, structure
STRUCTURE_REQUIRED = {
    "LONG":  ["bos_bull"],      # ou CHOCH BULLISH em structure
    "SHORT": ["bos_bear"],      # ou CHOCH BEARISH em structure
}


def has_structural_confirmation(direction: str, ms: dict) -> bool:
    """
    C1 — Valida se existe ao menos uma confirmação estrutural (BOS ou CHoCH)
    para a direção gerada. Sem BOS ou CHoCH, o sinal é ruído.
    """
    if direction in ("NEUTRO", "NO_TRADE"):
        return True   # neutro não precisa de confirmação
    structure = ms.get("structure", "")
    bos_bull  = bool(ms.get("bos_bull", False))
    bos_bear  = bool(ms.get("bos_bear", False))
    choch     = bool(ms.get("choch", False))
    if direction == "LONG":
        choch_bull = choch and "CHOCH BULLISH" in structure
        return bos_bull or choch_bull
    if direction == "SHORT":
        choch_bear = choch and "CHOCH BEARISH" in structure
        return bos_bear or choch_bear
    return True


def _structural_label(direction: str, ms: dict) -> str:
    """
    C5 — Texto de confirmação estrutural para o modal de detalhe.
    Exemplo: "BOS Bear ✓", "CHoCH Bear ✓", "Nenhuma ✗"
    """
    structure = ms.get("structure", "")
    if direction == "LONG":
        if ms.get("bos_bull"):
            return "BOS Bull ✓"
        if ms.get("choch") and "CHOCH BULLISH" in structure:
            return "CHoCH Bull ✓"
    elif direction == "SHORT":
        if ms.get("bos_bear"):
            return "BOS Bear ✓"
        if ms.get("choch") and "CHOCH BEARISH" in structure:
            return "CHoCH Bear ✓"
    return "Nenhuma ✗"


def _read_btc_bias() -> str:
    """
    C3 — Lê o bias do BTC em dashboard/current_state.json.
    Usa btc.smart_money.bias (campo real do dashboard).
    Retorna "NEUTRO" em caso de erro — nunca falha.
    """
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        return state.get("btc", {}).get("smart_money", {}).get("bias", "NEUTRO")
    except Exception:
        return "NEUTRO"


def is_btc_correlated_short(direction: str, score: float, btc_bias: str) -> bool:
    """
    C3 — True se o SHORT é reflexo de mercado bearish, não sinal SMC independente.
    Ativos com score >= 55 têm análise própria suficiente — não bloquear.
    """
    return (
        direction == "SHORT"
        and score < SCORE_THRESHOLDS["actionable"]
        and btc_bias == "BEARISH"
    )


def _ob_count_real(ob: dict) -> int:
    """
    C4 — Conta OBs reais (0-2) em vez de chaves do dict (bug anterior = 6-12).
    detect_order_blocks() retorna um dict ou None por direção, nunca uma lista.
    """
    n = 0
    if ob.get("bullish_ob") is not None:
        n += 1
    if ob.get("bearish_ob") is not None:
        n += 1
    return n


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

TOP_RESULTS    = 50    # máximo de coins no resultado (todas escaneadas)
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

        # MEXC retorna 8 colunas (sem trades/taker fields)
        n_cols = len(raw[0])
        base_cols = ["timestamp", "open", "high", "low", "close", "volume",
                     "close_time", "quote_volume", "trades", "taker_buy_base",
                     "taker_buy_quote", "ignore"]
        df = pd.DataFrame(raw, columns=base_cols[:n_cols])
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

def _quick_smc(df: pd.DataFrame, symbol: str, btc_bias: str = "NEUTRO") -> Optional[dict]:
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

        # ── C1: Validação estrutural obrigatória ───────────────────────────
        filtered_reason = None
        try:
            if direction in ("LONG", "SHORT") and not has_structural_confirmation(direction, ms):
                filtered_reason = "no_structural_confirmation"
                direction       = "NO_TRADE"
        except Exception as _c1_err:
            log.debug(f"[C1] has_structural_confirmation falhou: {_c1_err}")

        # ── C3: Filtro de correlação BTC ───────────────────────────────────
        try:
            if direction == "SHORT" and is_btc_correlated_short(direction, score, btc_bias):
                filtered_reason = "btc_correlation"
                direction       = "NO_TRADE"
        except Exception as _c3_err:
            log.debug(f"[C3] is_btc_correlated_short falhou: {_c3_err}")

        # ── F2: Filtro de sessão institucional ─────────────────────────────
        session_info: dict = {"allowed": True, "session_name": "", "next_session": "",
                              "reason": "default", "bypassed": False}
        try:
            from session_filter import apply_session_filter
            session_info = apply_session_filter(direction=direction, score=score)
            if not session_info["allowed"]:
                # Loga rejeição em logs/rejected_signals_YYYY-MM-DD.jsonl
                try:
                    LOGS_DIR.mkdir(parents=True, exist_ok=True)
                    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    log_path  = LOGS_DIR / f"rejected_signals_{date_str}.jsonl"
                    log_entry = json.dumps({
                        "ts":     datetime.now(timezone.utc).isoformat(),
                        "symbol": symbol,
                        "reason": "out_of_session",
                        "direction": direction,
                        "score":  round(score, 1),
                        "next_session": session_info.get("next_session", ""),
                    })
                    with open(log_path, "a") as _lf:
                        _lf.write(log_entry + "\n")
                except Exception as _log_err:
                    log.debug(f"[F2] log rejeição falhou: {_log_err}")
                filtered_reason = "out_of_session"
                direction       = "NO_TRADE"
        except Exception as _sf_err:
            log.debug(f"[F2] session_filter falhou: {_sf_err}")

        # ── C4: ob_count real (corrige bug de contar chaves do dict) ───────
        ob_count = _ob_count_real(ob)

        # ── C5: Label de confirmação estrutural (para o modal) ─────────────
        struct_confirm = _structural_label(
            direction if direction not in ("NEUTRO", "NO_TRADE") else
            ("LONG" if score >= 60 else "SHORT" if score <= 40 else "NEUTRO"),
            ms
        )

        # ── C2: should_alert — C1 passou E score >= telegram ──────────────
        should_alert = (
            direction in ("LONG", "SHORT")
            and score >= SCORE_THRESHOLDS["telegram"]
        )
        score_zone = (
            "signal"      if score >= SCORE_THRESHOLDS["telegram"]
            else "watch"  if score >= SCORE_THRESHOLDS["display"]
            else "noise"
        )

        # Preço atual
        price = float(df["close"].iloc[-1])

        # Sinal básico via ATR (sem MTF/CCXT — usa só candles REST)
        # Apenas gera níveis para sinais aprovados por C1
        entry = stop = tp1 = tp2 = None
        if direction in ("LONG", "SHORT"):
            try:
                atr = float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])
                if direction == "LONG":
                    entry = round(price * 1.001, 6)
                    stop  = round(price - atr * 1.5, 6)
                    tp1   = round(price + atr * 2.0, 6)
                    tp2   = round(price + atr * 4.0, 6)
                elif direction == "SHORT":
                    entry = round(price * 0.999, 6)
                    stop  = round(price + atr * 1.5, 6)
                    tp1   = round(price - atr * 2.0, 6)
                    tp2   = round(price - atr * 4.0, 6)
            except Exception:
                pass

        # Variação 24h estimada (última vs 96 candles atrás em 15m)
        if len(df) >= 96:
            price_24h_ago = float(df["close"].iloc[-96])
            change_24h    = round((price - price_24h_ago) / price_24h_ago * 100, 2)
        else:
            change_24h = 0.0

        fvg_count = (fvg.get("total_bull") or 0) + (fvg.get("total_bear") or 0) if isinstance(fvg, dict) else 0

        return {
            "symbol":       symbol.replace("USDT", ""),
            "pair":         f"{symbol.replace('USDT', '')}/USDT",
            "price":        price,
            "change_24h":   change_24h,
            "smc_score":    round(score, 1),
            # F1/F2: display_score=None para sinais sem confirmação estrutural ou fora de sessão
            # score_raw preserva o valor original para logs e cálculos internos
            "display_score": None if filtered_reason in ("no_structural_confirmation", "out_of_session") else round(score, 1),
            "score_raw":    round(score, 1),
            "direction":    direction,
            "bias":         bias,
            "structure":    structure,
            "bos_bull":     ms.get("bos_bull", False),
            "bos_bear":     ms.get("bos_bear", False),
            "choch":        ms.get("choch", False),
            "ob_count":     ob_count,             # C4: conta OBs reais (0-2), não chaves de dict
            "fvg_count":    fvg_count,
            "entry":        entry,
            "stop":         stop,
            "tp1":          tp1,
            "tp2":          tp2,
            "conviction":   round(abs(score - 50), 1),
            # Campos novos
            "filtered_reason":   filtered_reason,    # C1/C3/F2: motivo do filtro
            "should_alert":      should_alert,        # C2: elegível para Telegram
            "score_zone":        score_zone,          # C2: "signal"|"watch"|"noise"
            "struct_confirm":    struct_confirm,      # C5: label para o modal
            "session_info":      session_info,        # F2: info da sessão ativa
        }
    except Exception as e:
        log.debug(f"[altcoin_scanner] SMC {symbol}: {e}")
        return None


# ── Scanner principal ────────────────────────────────────────────────────────

def run_altcoin_scan() -> dict:
    """
    Escaneia SCAN_SYMBOLS com SMC engine e retorna top N por convicção.

    Correções ativas (2026-04):
    - C1: filtro de confirmação estrutural (BOS/CHoCH obrigatório)
    - C2: threshold de display (score < 45 oculto do dashboard)
    - C3: filtro de correlação BTC (SHORT fraco em mercado bearish → NO_TRADE)
    - C4: ob_count corrigido para contar OBs reais (não chaves de dict)
    - F1: display_score=None para cards sem confirmação estrutural
    - F2: session filter — bloqueia sinais fora de janelas institucionais (bypass score≥75)

    Retorna dict com:
        scan_ts      — ISO timestamp
        coins_scanned — total processado
        candidates   — lista ordenada por convicção (score >= 45)
    """
    log.info(f"[altcoin_scanner] Iniciando scan de {len(SCAN_SYMBOLS)} coins ...")

    # C3: lê bias do BTC uma vez antes do loop (evita I/O por coin)
    try:
        btc_bias = _read_btc_bias()
    except Exception:
        btc_bias = "NEUTRO"
    log.info(f"[altcoin_scanner] BTC bias: {btc_bias}")

    # F2: snapshot do estado de sessão no início do scan (para o dashboard)
    try:
        from session_filter import _current_session_name, _next_session_label, is_valid_session
        _now = datetime.now(timezone.utc)
        _session_state = {
            "in_session":   is_valid_session(_now),
            "session_name": _current_session_name(_now),
            "next_session": _next_session_label(_now),
        }
    except Exception:
        _session_state = {"in_session": False, "session_name": "", "next_session": ""}

    candidates = []
    raw_count  = 0  # total antes dos filtros de display

    for sym in SCAN_SYMBOLS:
        df = _fetch_klines(sym, interval="15m", limit=250)
        if df is None or len(df) < 50:
            log.debug(f"[altcoin_scanner] {sym}: dados insuficientes")
            time.sleep(REQUEST_DELAY)
            continue

        result = _quick_smc(df, sym, btc_bias=btc_bias)
        if result:
            raw_count += 1
            # C2: oculta ruído — score abaixo de display threshold não vai ao dashboard
            if result["smc_score"] < SCORE_THRESHOLDS["display"]:
                log.debug(
                    f"[altcoin_scanner] {sym}: score={result['smc_score']} < {SCORE_THRESHOLDS['display']} "
                    f"— descartado (ruído)"
                )
                time.sleep(REQUEST_DELAY)
                continue
            candidates.append(result)
            log.debug(
                f"[altcoin_scanner] {sym}: score={result['smc_score']} "
                f"dir={result['direction']} zone={result['score_zone']} "
                f"filter={result.get('filtered_reason')} conv={result['conviction']}"
            )
        time.sleep(REQUEST_DELAY)

    # Ordena: LONG/SHORT aprovados (C1+C3 passaram) primeiro, depois NO_TRADE/NEUTRO
    def sort_key(c):
        base = c["conviction"]
        # prioriza sinais com entrada definida e aprovados
        if c["direction"] in ("LONG", "SHORT") and c.get("entry"):
            base += 5
        # rebaixa NO_TRADE filtrados
        if c.get("filtered_reason"):
            base -= 3
        return base

    candidates.sort(key=sort_key, reverse=True)

    result = {
        "scan_ts":         datetime.now(timezone.utc).isoformat(),
        "coins_scanned":   raw_count,
        "coins_displayed": len(candidates),
        "btc_bias":        btc_bias,
        "session_state":   _session_state,   # F2: estado de sessão no momento do scan
        "candidates":      candidates[:TOP_RESULTS],
    }
    log.info(
        f"[altcoin_scanner] Concluído: {raw_count} coins escaneadas | "
        f"{len(candidates)} exibíveis (score >= {SCORE_THRESHOLDS['display']}) | "
        f"top={[c['symbol'] for c in candidates[:5]]}"
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json
    r = run_altcoin_scan()
    print(json.dumps(r, indent=2, default=str))
