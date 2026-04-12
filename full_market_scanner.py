"""
full_market_scanner.py — Full Market Scanner (MEXC Futures)

Varre todos os contratos ativos (~300–850 símbolos) a cada 90 segundos,
detecta setups de pump e crash via 6 detectores em dois estágios,
e dispara alertas Telegram com score, tier e contexto.

Sistema paralelo e independente — não altera Pump Radar nem Crash Radar.

Campos MEXC confirmados via probe (Passo 2):
  ticker : symbol, lastPrice, holdVol, volume24, fundingRate,
           riseFallRate, riseFallRates (r7/r30/r90/r180/r365)
  deals  : p (price), v (volume), T (1=buy, 2=sell), t (timestamp)
  depth  : asks/bids = [[price, size, count], ...]
  klines : {time, open, close, high, low, vol, amount, ...}

Filtro de contrato ativo: apiAllowed==True  (state==0 para TODOS os contratos)
"""

import json
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import requests

logger = logging.getLogger(__name__)

BASE_DIR      = Path(__file__).parent
FMS_SCAN_FILE = BASE_DIR / "dashboard" / "full_market_scan.json"
MEXC_BASE     = "https://contract.mexc.com/api/v1/contract"


# ══════════════════════════════════════════════════════════════════════════════
# C3: Constantes e funções module-level — níveis de trade, DNA, notas táticas
# ══════════════════════════════════════════════════════════════════════════════

_LEVELS: dict = {
    "PUMP": {
        "stop_pct": -0.015,   # -1.5%
        "tp1_pct":  +0.022,   # +2.2%
        "tp2_pct":  +0.042,   # +4.2%
        "tp3_pct":  +0.064,   # +6.4%
    },
    "CRASH": {
        "stop_pct": +0.015,   # +1.5%
        "tp1_pct":  -0.022,   # -2.2%
        "tp2_pct":  -0.042,   # -4.2%
        "tp3_pct":  -0.064,   # -6.4%
    },
}

_PROBABILITY: dict = {
    "CRÍTICO": 76,
    "ALTO":    67,
    "MÉDIO":   59,
}

_DNA_MAP: dict = {
    "PUMP": {
        "d1": "Funding Squeeze",
        "d2": "OI Breakout",
        "d3": "Volume Surge",
        "d4": "CVD Pressure",
        "d5": "Liquidity Hunt",
        "d6": "Volatility Coil",
    },
    "CRASH": {
        "d1": "Funding Overload",
        "d2": "OI Unwind",
        "d3": "Volume Dump",
        "d4": "CVD Divergence",
        "d5": "Liquidity Sweep",
        "d6": "Volatility Coil",
    },
}

_TACTICAL_NOTES: dict = {
    "PUMP": {
        "CRÍTICO": "Entrada imediata com gestão de risco definida",
        "ALTO":    "Aguardar confirmação de vela antes de entrar",
        "MÉDIO":   "Setup preliminar. Aguardar confirmação adicional.",
    },
    "CRASH": {
        "CRÍTICO": "Fechar longs. Considerar short com confirmação",
        "ALTO":    "Reduzir exposição. Monitorar próximos 10min",
        "MÉDIO":   "Setup preliminar. Aguardar confirmação adicional.",
    },
}


def calculate_trade_levels(price: float, dominant_type: str, tier: str) -> dict:
    """
    Calcula Entry/Stop/TP1/TP2/TP3 com base no tipo dominante e tier.
    Retorna dict com entry, stop, tp1, tp2, tp3, prob_pct, move_pct.
    """
    lvl   = _LEVELS.get(dominant_type, _LEVELS["PUMP"])
    entry = price
    stop  = round(price * (1.0 + lvl["stop_pct"]), 6)
    tp1   = round(price * (1.0 + lvl["tp1_pct"]),  6)
    tp2   = round(price * (1.0 + lvl["tp2_pct"]),  6)
    tp3   = round(price * (1.0 + lvl["tp3_pct"]),  6)
    prob  = _PROBABILITY.get(tier, 59)
    move_pct = abs(lvl["tp3_pct"]) * 100   # ex: 6.4
    return {
        "entry":    entry,
        "stop":     stop,
        "tp1":      tp1,
        "tp2":      tp2,
        "tp3":      tp3,
        "prob_pct": prob,
        "move_pct": move_pct,
    }


def _get_dna_label(detectors: dict, dominant_type: str) -> str:
    """
    Retorna label do setup baseado no detector com maior score
    para o tipo dominante. Fallback: 'Multi-Signal'.
    """
    dna_map  = _DNA_MAP.get(dominant_type, _DNA_MAP["PUMP"])
    key_map  = "pump" if dominant_type == "PUMP" else "crash"
    best_key   = None
    best_score = 0.0

    for det_key in dna_map:
        d = detectors.get(det_key, {})
        # D3 e D6 são agnósticos (campo "score")
        if "score" in d:
            score = float(d.get("score", 0))
        else:
            score = float(d.get(key_map, 0))
        if score > best_score:
            best_score = score
            best_key   = det_key

    return dna_map.get(best_key, "Multi-Signal") if best_key else "Multi-Signal"


# ══════════════════════════════════════════════════════════════════════════════
# MarketMemory — histórico em RAM, cooldowns, aceleração de score
# ══════════════════════════════════════════════════════════════════════════════
class MarketMemory:
    """
    Guarda os últimos 10 scans por símbolo em RAM.
    Sem I/O de disco no ciclo principal.
    """
    _MAX_ENTRIES = 10

    def __init__(self):
        self._history:   dict = {}   # symbol → deque[dict]
        self._cooldowns: dict = {}   # symbol → timestamp UTC (float)

    def record(self, symbol: str, pump_score: float, crash_score: float,
               hold_vol: float, volume_usd: float) -> None:
        """Salva entrada com timestamp UTC. Deque de máx 10 entradas por símbolo."""
        if symbol not in self._history:
            self._history[symbol] = deque(maxlen=self._MAX_ENTRIES)
        self._history[symbol].append({
            "pump_score":  pump_score,
            "crash_score": crash_score,
            "hold_vol":    hold_vol,
            "volume_usd":  volume_usd,
            "ts":          time.time(),
        })

    def get_acceleration(self, symbol: str, score_type: str) -> float:
        """score_atual - score_anterior. Retorna 0.0 se histórico < 2 entradas."""
        h = self._history.get(symbol)
        if not h or len(h) < 2:
            return 0.0
        entries = list(h)
        return entries[-1][score_type] - entries[-2][score_type]

    def get_streak(self, symbol: str, score_type: str, threshold: float) -> int:
        """Scans consecutivos recentes com score >= threshold (do mais recente)."""
        h = self._history.get(symbol)
        if not h:
            return 0
        streak = 0
        for entry in reversed(list(h)):
            if entry[score_type] >= threshold:
                streak += 1
            else:
                break
        return streak

    def get_avg_volume(self, symbol: str) -> float:
        """Média de volume_usd dos últimos N scans. Retorna 0.0 sem histórico."""
        h = self._history.get(symbol)
        if not h:
            return 0.0
        vols = [e["volume_usd"] for e in h if e["volume_usd"] > 0]
        return sum(vols) / len(vols) if vols else 0.0

    def get_oi_previous(self, symbol: str) -> float:
        """holdVol do scan anterior. Retorna 0.0 sem histórico suficiente."""
        h = self._history.get(symbol)
        if not h or len(h) < 2:
            return 0.0
        return list(h)[-2]["hold_vol"]

    def is_in_cooldown(self, symbol: str, minutes: int) -> bool:
        """True se alerta foi enviado há menos de N minutos."""
        ts = self._cooldowns.get(symbol)
        if ts is None:
            return False
        return (time.time() - ts) < (minutes * 60)

    def set_cooldown(self, symbol: str) -> None:
        """Registra timestamp UTC do último alerta enviado."""
        self._cooldowns[symbol] = time.time()


# ══════════════════════════════════════════════════════════════════════════════
# QuickTriageScanner — Estágio 1: triagem via ticker (1 request)
# ══════════════════════════════════════════════════════════════════════════════
class QuickTriageScanner:
    """
    Filtra 300–850 contratos para 20–50 candidatos em < 30s
    usando apenas o endpoint de ticker (retorna todos em um único request).
    """
    MIN_VOLUME_USD    = 300_000   # excluir sem liquidez mínima
    ANOMALY_THRESHOLD = 12        # score mínimo para passar ao Estágio 2

    # Ações sintéticas, ETFs e commodities — excluídos por não serem crypto spot/perp
    EXCLUDED_KEYWORDS = [
        'STOCK', 'ETF', 'US30', 'ALUMINUM', 'COPPER', 'GOLD', 'SILVER',
        'NVIDIA', 'NVDA', 'TESLA', 'TSLA', 'APPLE', 'AAPL', 'AMAZON', 'AMZN',
        'GOOGLE', 'GOOGL', 'META', 'MSTR', 'COIN', 'COINBASE', 'MSFT',
        'MICROSOFT', 'NETFLIX', 'NFLX', 'AMD', 'INTEL', 'INTC', 'BABA',
        'OIL', 'CRUDE', 'NATGAS', 'WHEAT', 'CORN', 'PLATINUM', 'PALLADIUM',
        'EWY', 'SPX', 'NDX', 'DJI', 'VIX', 'PIPPI', 'STABLE', 'RIVER',
    ]

    def __init__(self, memory: MarketMemory):
        self.memory      = memory
        self._last_total = 0   # total de contratos examinados no último run()

    def _is_synthetic(self, symbol: str) -> bool:
        """True se o símbolo é stock sintética, ETF ou commodity — deve ser excluído."""
        return any(kw in symbol for kw in self.EXCLUDED_KEYWORDS)

    def get_active_symbols(self) -> list:
        """
        GET /api/v1/contract/detail → filtrar apiAllowed==True.
        (state==0 para todos os contratos — apiAllowed é o filtro real)
        """
        try:
            r       = requests.get(f"{MEXC_BASE}/detail", timeout=10)
            data    = r.json().get("data", [])
            symbols = [c["symbol"] for c in data if c.get("apiAllowed") is True]
            logger.info(f"[TRIAGE] {len(symbols)} contratos ativos (apiAllowed)")
            return symbols
        except Exception as e:
            logger.error(f"[TRIAGE] get_active_symbols erro: {e}")
            return []

    def fetch_all_tickers(self) -> dict:
        """GET /api/v1/contract/ticker → único request, retorna todos. Keyed por symbol."""
        try:
            r    = requests.get(f"{MEXC_BASE}/ticker", timeout=10)
            data = r.json().get("data", [])
            return {item["symbol"]: item for item in data}
        except Exception as e:
            logger.error(f"[TRIAGE] fetch_all_tickers erro: {e}")
            return {}

    def _volume_usd(self, ticker: dict) -> float:
        """amount24 = volume em USD já calculado pela exchange (correto para qualquer contractSize)."""
        try:
            return float(ticker["amount24"])
        except Exception:
            return 0.0

    def _anomaly_score(self, ticker: dict, symbol: str) -> dict:
        """
        Score rápido 0–36 usando apenas campos do ticker.
        Sem requests extras.
        """
        try:
            fr      = float(ticker.get("fundingRate", 0) or 0)
            rfr     = float(ticker.get("riseFallRate", 0) or 0)
            vol_usd = self._volume_usd(ticker)

            # funding_score (0–12)
            afr = abs(fr)
            if   afr > 0.005:  funding_score = 12
            elif afr > 0.002:  funding_score = 8
            elif afr > 0.0005: funding_score = 4
            else:              funding_score = 0

            # volume_score (0–12)
            avg = self.memory.get_avg_volume(symbol)
            if avg > 0:
                ratio = vol_usd / avg
                if   ratio > 5.0: volume_score = 12
                elif ratio > 3.0: volume_score = 8
                elif ratio > 1.8: volume_score = 4
                else:             volume_score = 0
            else:
                # sem histórico: usar variação de preço como proxy
                volume_score = 6 if abs(rfr) > 0.08 else 0

            # price_score (0–12)
            if   abs(rfr) > 0.20: price_score = 12
            elif abs(rfr) > 0.10: price_score = 8
            elif abs(rfr) > 0.04: price_score = 4
            else:                 price_score  = 0

            # direction_hint
            if   fr < -0.002: direction_hint = "PUMP"    # shorts excessivos
            elif fr >  0.002: direction_hint = "CRASH"   # longs excessivos
            else:             direction_hint = "UNKNOWN"

            return {
                "anomaly_score":  funding_score + volume_score + price_score,
                "funding_rate":   fr,
                "volume_usd":     vol_usd,
                "price_change":   rfr,
                "direction_hint": direction_hint,
            }
        except Exception as e:
            return {
                "anomaly_score": 0, "funding_rate": 0.0, "volume_usd": 0.0,
                "price_change": 0.0, "direction_hint": "UNKNOWN", "error": str(e),
            }

    def run(self) -> list:
        """
        1. Busca símbolos ativos + todos os tickers
        2. Filtra por volume mínimo e anomaly_score
        3. Ordena por anomaly_score desc
        Retorna lista de dicts: {symbol, ticker, anomaly}
        """
        t0      = time.time()
        symbols = self.get_active_symbols()
        tickers = self.fetch_all_tickers()

        # total de contratos que temos tickers para examinar
        self._last_total = len(tickers)

        # usar a intersecção: símbolo na lista de ativos E no ticker
        symbol_set = set(symbols) if symbols else set(tickers.keys())

        candidates = []
        for symbol in symbol_set:
            if self._is_synthetic(symbol):
                continue
            ticker = tickers.get(symbol)
            if not ticker:
                continue
            vol_usd = self._volume_usd(ticker)
            if vol_usd < self.MIN_VOLUME_USD:
                continue
            anomaly = self._anomaly_score(ticker, symbol)
            if anomaly["anomaly_score"] < self.ANOMALY_THRESHOLD:
                continue
            candidates.append({"symbol": symbol, "ticker": ticker, "anomaly": anomaly})

        candidates.sort(key=lambda x: x["anomaly"]["anomaly_score"], reverse=True)
        elapsed = time.time() - t0
        logger.info(
            f"[TRIAGE] {len(candidates)} candidatos de {self._last_total} "
            f"ativos ({elapsed:.1f}s)"
        )
        return candidates


# ══════════════════════════════════════════════════════════════════════════════
# Detectores D1–D6
# Contrato: retorna dict com pump/crash (0–25) ou score (0–25) + details
# Todo o corpo em try/except — falha retorna score 0
# ══════════════════════════════════════════════════════════════════════════════

class FundingExtremeDetector:
    """
    D1 — Funding extremo com contexto r30/r7.
    Fonte: ticker['fundingRate'] + ticker['riseFallRates'] — sem request extra.

    fr < 0 = shorts sobrecarregados = pump iminente
    fr > 0 = longs sobrecarregados  = crash iminente

    Multiplicador de contexto (pump_score):
      r30 < -0.20 + funding negativo → squeeze clássico       → ×1.3
      r30 > +0.50 + funding negativo → rally estrutural, não squeeze → ×0.6
      demais                                                   → ×1.0
    """

    def detect(self, ticker: dict, **_) -> dict:
        try:
            fr  = float(ticker.get("fundingRate", 0) or 0)
            afr = abs(fr)

            rates = ticker.get("riseFallRates") or {}
            r30   = float(rates.get("r30") or 0)
            r7    = float(rates.get("r7")  or 0)

            # score base (0–25)
            if   fr < -0.008: pump_score  = 25
            elif fr < -0.005: pump_score  = 18
            elif fr < -0.003: pump_score  = 12
            elif fr < -0.001: pump_score  = 5
            else:             pump_score  = 0

            if   fr >  0.008: crash_score = 25
            elif fr >  0.005: crash_score = 18
            elif fr >  0.003: crash_score = 12
            elif fr >  0.001: crash_score = 5
            else:             crash_score = 0

            # multiplicador de contexto para pump_score
            if r30 < -0.20 and fr < -0.003:
                context_mult = 1.3   # caiu muito + shorts excessivos = squeeze clássico
            elif r30 > 0.50 and fr < -0.003:
                context_mult = 0.6   # já subiu muito + funding negativo = estrutural
            else:
                context_mult = 1.0

            pump_score = min(25, round(pump_score * context_mult))

            return {
                "pump":    pump_score,
                "crash":   crash_score,
                "details": {
                    "funding_rate":   fr,
                    "extreme":        afr > 0.003,
                    "r30":            r30,
                    "r7":             r7,
                    "context_mult":   context_mult,
                },
            }
        except Exception as e:
            return {"pump": 0, "crash": 0, "details": {"error": str(e)}}


class OIAccelerationDetector:
    """
    D2 — Aceleração de Open Interest.
    Fonte: ticker['holdVol'] + memory.get_oi_previous(symbol).
    OI crescendo = acumulação institucional → pump
    OI caindo + preço caindo = liquidação em cascata → crash
    """

    def detect(self, ticker: dict, symbol: str, memory: MarketMemory, **_) -> dict:
        try:
            oi_now  = float(ticker.get("holdVol", 0) or 0)
            oi_prev = memory.get_oi_previous(symbol)
            rfr     = float(ticker.get("riseFallRate", 0) or 0)

            oi_chg = (oi_now - oi_prev) / oi_prev if oi_prev > 0 else 0.0

            if   oi_chg > 0.25: pump_score = 25
            elif oi_chg > 0.15: pump_score = 20
            elif oi_chg > 0.08: pump_score = 13
            elif oi_chg > 0.03: pump_score = 6
            else:               pump_score = 0

            crash_score = 0
            if rfr < -0.03 and oi_chg < -0.05:
                if   oi_chg < -0.20: crash_score = 25
                elif oi_chg < -0.12: crash_score = 16
                else:                crash_score = 8   # oi_chg < -0.05

            return {
                "pump":    pump_score,
                "crash":   crash_score,
                "details": {
                    "oi_current":    oi_now,
                    "oi_previous":   oi_prev,
                    "oi_change_pct": round(oi_chg * 100, 2),
                },
            }
        except Exception as e:
            return {"pump": 0, "crash": 0, "details": {"error": str(e)}}


class VolumeSpikeDetector:
    """
    D3 — Volume spike (agnóstico a direção).
    Fonte: GET /api/v1/contract/kline/{symbol}?interval=Min15&limit=20
    Campo de volume confirmado: 'vol'
    """

    def detect(self, symbol: str, **_) -> dict:
        try:
            r = requests.get(
                f"{MEXC_BASE}/kline/{symbol}",
                params={"interval": "Min15", "limit": 20},
                timeout=8,
            )
            kdata = r.json().get("data", {})
            vols  = kdata.get("vol", [])

            if len(vols) < 2:
                return {"score": 0, "details": {"error": "klines insuficientes"}}

            base_vols = [float(v) for v in vols[:-1]]
            last_vol  = float(vols[-1])
            avg       = mean(base_vols) if base_vols else 0.0
            ratio     = (last_vol / avg) if avg > 0 else 1.0

            if   ratio > 8.0: score = 25
            elif ratio > 5.0: score = 20
            elif ratio > 3.5: score = 14
            elif ratio > 2.0: score = 7
            else:             score = 0

            return {
                "score":   score,
                "details": {
                    "volume_ratio": round(ratio, 2),
                    "avg_volume":   round(avg, 2),
                    "last_volume":  last_vol,
                },
            }
        except Exception as e:
            return {"score": 0, "details": {"error": str(e)}}


class CVDDivergenceDetector:
    """
    D4 — Cumulative Volume Delta.
    Fonte: GET /api/v1/contract/deals/{symbol}?limit=100
    Campo direção confirmado: T (1=buy, 2=sell). Volume: v.
    """

    def detect(self, symbol: str, **_) -> dict:
        try:
            r = requests.get(
                f"{MEXC_BASE}/deals/{symbol}",
                params={"limit": 100},
                timeout=8,
            )
            trades   = r.json().get("data", [])
            buy_vol  = 0.0
            sell_vol = 0.0

            for t in trades:
                v    = float(t.get("v", 0) or 0)
                side = t.get("T", 0)   # 1=buy, 2=sell
                if side == 1:
                    buy_vol  += v
                elif side == 2:
                    sell_vol += v

            total     = buy_vol + sell_vol
            cvd_ratio = (buy_vol / total) if total > 0 else 0.5

            if   cvd_ratio > 0.87: pump_score  = 25
            elif cvd_ratio > 0.80: pump_score  = 21
            elif cvd_ratio > 0.70: pump_score  = 14
            elif cvd_ratio > 0.60: pump_score  = 7
            else:                  pump_score  = 0

            if   cvd_ratio < 0.13: crash_score = 25
            elif cvd_ratio < 0.20: crash_score = 21
            elif cvd_ratio < 0.30: crash_score = 14
            elif cvd_ratio < 0.40: crash_score = 7
            else:                  crash_score = 0

            return {
                "pump":    pump_score,
                "crash":   crash_score,
                "details": {
                    "cvd_ratio":    round(cvd_ratio, 4),
                    "buy_vol":      buy_vol,
                    "sell_vol":     sell_vol,
                    "trades_count": len(trades),
                },
            }
        except Exception as e:
            return {"pump": 0, "crash": 0, "details": {"error": str(e)}}


class LiquidityClusterDetector:
    """
    D5 — Cluster de liquidez no orderbook.
    Fonte: GET /api/v1/contract/depth/{symbol}?limit=20
    Estrutura confirmada: asks/bids = [[price, size, count], ...]
    """

    def detect(self, ticker: dict, symbol: str, **_) -> dict:
        try:
            r = requests.get(
                f"{MEXC_BASE}/depth/{symbol}",
                params={"limit": 20},
                timeout=8,
            )
            depth      = r.json().get("data", {})
            asks       = depth.get("asks", [])   # [[price, size, count], ...]
            bids       = depth.get("bids", [])
            curr_price = float(ticker.get("lastPrice", 0) or 0)

            if not asks or not bids or curr_price <= 0:
                return {"pump": 0, "crash": 0, "details": {"error": "depth vazio"}}

            def _find_cluster(levels: list) -> tuple:
                """Retorna (cluster_price, cluster_usd) do cluster mais próximo."""
                # converter tamanho para USD: size * price
                level_usds = [float(lvl[1]) * float(lvl[0]) for lvl in levels]
                if not level_usds:
                    return 0.0, 0.0
                avg_level = mean(level_usds)
                clusters  = [
                    (float(lvl[0]), usd)
                    for lvl, usd in zip(levels, level_usds)
                    if usd > avg_level * 3
                ]
                if not clusters:
                    return 0.0, 0.0
                # cluster mais próximo do preço atual
                closest = min(clusters, key=lambda x: abs(x[0] - curr_price))
                return closest[0], closest[1]

            ask_price, ask_usd = _find_cluster(asks)
            bid_price, bid_usd = _find_cluster(bids)

            # pump: cluster de asks acima do preço atual
            pump_score = 0
            if ask_price > curr_price:
                dist = (ask_price - curr_price) / curr_price
                if   dist < 0.02: pump_score = 25
                elif dist < 0.04: pump_score = 18
                elif dist < 0.07: pump_score = 12
                elif dist < 0.12: pump_score = 6

            # crash: cluster de bids abaixo do preço atual
            crash_score = 0
            if 0 < bid_price < curr_price:
                dist = (curr_price - bid_price) / curr_price
                if   dist < 0.02: crash_score = 25
                elif dist < 0.04: crash_score = 18
                elif dist < 0.07: crash_score = 12
                elif dist < 0.12: crash_score = 6

            return {
                "pump":    pump_score,
                "crash":   crash_score,
                "details": {
                    "ask_cluster_price": ask_price,
                    "ask_cluster_usd":   round(ask_usd, 2),
                    "bid_cluster_price": bid_price,
                    "bid_cluster_usd":   round(bid_usd, 2),
                },
            }
        except Exception as e:
            return {"pump": 0, "crash": 0, "details": {"error": str(e)}}


class VolatilityCompressionDetector:
    """
    D6 — Compressão de volatilidade (agnóstico a direção).
    Fonte: GET /api/v1/contract/kline/{symbol}?interval=Min60&limit=48
    Campos confirmados: 'high', 'low'
    """

    def detect(self, symbol: str, **_) -> dict:
        try:
            r = requests.get(
                f"{MEXC_BASE}/kline/{symbol}",
                params={"interval": "Min60", "limit": 48},
                timeout=8,
            )
            kdata = r.json().get("data", {})
            highs = kdata.get("high", [])
            lows  = kdata.get("low",  [])

            if len(highs) < 4 or len(lows) < 4:
                return {"score": 0, "details": {"error": "klines insuficientes"}}

            ranges = [float(h) - float(l) for h, l in zip(highs, lows)]
            mid    = len(ranges) // 2
            prev   = ranges[:mid]
            recent = ranges[mid:]

            avg_prev   = mean(prev)   if prev   else 0.0
            avg_recent = mean(recent) if recent else 0.0

            compression = (avg_recent / avg_prev) if avg_prev > 0 else 1.0

            if   compression < 0.45: score = 25
            elif compression < 0.60: score = 18
            elif compression < 0.72: score = 12
            elif compression < 0.83: score = 6
            else:                    score = 0

            return {
                "score":   score,
                "details": {
                    "compression_ratio":  round(compression, 4),
                    "avg_range_recent":   round(avg_recent, 4),
                    "avg_range_previous": round(avg_prev, 4),
                },
            }
        except Exception as e:
            return {"score": 0, "details": {"error": str(e)}}


# ══════════════════════════════════════════════════════════════════════════════
# DeepAnalysisEngine — Estágio 2 (D1–D6 em paralelo)
# ══════════════════════════════════════════════════════════════════════════════
class DeepAnalysisEngine:
    """
    Executa os 6 detectores em paralelo via ThreadPoolExecutor(max_workers=6).
    Normaliza para 0–100 (máximo teórico: 6 × 25 = 150 pts).
    """

    def __init__(self, memory: MarketMemory):
        self.memory = memory
        self.d1     = FundingExtremeDetector()
        self.d2     = OIAccelerationDetector()
        self.d3     = VolumeSpikeDetector()
        self.d4     = CVDDivergenceDetector()
        self.d5     = LiquidityClusterDetector()
        self.d6     = VolatilityCompressionDetector()

    def analyze(self, candidate: dict) -> dict:
        symbol  = candidate["symbol"]
        ticker  = candidate["ticker"]
        anomaly = candidate["anomaly"]

        # contexto compartilhado — cada detector usa o que precisa via **kwargs
        ctx = {"symbol": symbol, "ticker": ticker, "memory": self.memory}

        detectors = [
            ("d1", self.d1.detect),
            ("d2", self.d2.detect),
            ("d3", self.d3.detect),
            ("d4", self.d4.detect),
            ("d5", self.d5.detect),
            ("d6", self.d6.detect),
        ]

        results = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(fn, **ctx): name for name, fn in detectors}
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    results[name] = fut.result()
                except Exception as e:
                    results[name] = {
                        "pump": 0, "crash": 0, "score": 0,
                        "details": {"error": str(e)},
                    }

        def _p(k: str) -> float:
            """Extrai score pump (ou score agnóstico) do detector k."""
            d = results.get(k, {})
            v = d.get("pump", d.get("score", 0))
            return float(v) if isinstance(v, (int, float)) else 0.0

        def _c(k: str) -> float:
            """Extrai score crash (ou score agnóstico) do detector k."""
            d = results.get(k, {})
            v = d.get("crash", d.get("score", 0))
            return float(v) if isinstance(v, (int, float)) else 0.0

        # C2: Cap D5 contribution — evita falsos positivos quando D5 não tem confirmação
        # others_pump/crash = soma de todos exceto D5
        others_pump  = _p("d1") + _p("d2") + _p("d3") + _p("d4") + _p("d6")
        others_crash = _c("d1") + _c("d2") + _c("d3") + _c("d4") + _c("d6")

        d5_pump_eff  = _p("d5")
        d5_crash_eff = _c("d5")

        # Se demais detectores são fracos (< 8 pts), D5 sozinho não pode ultrapassar 12 pts
        if others_pump  < 8 and d5_pump_eff  > 12:
            d5_pump_eff  = 12.0
        if others_crash < 8 and d5_crash_eff > 12:
            d5_crash_eff = 12.0

        # D3 e D6 são agnósticos: contribuem igual para pump e crash
        raw_pump  = _p("d1") + _p("d2") + _p("d3") + _p("d4") + d5_pump_eff  + _p("d6")
        raw_crash = _c("d1") + _c("d2") + _c("d3") + _c("d4") + d5_crash_eff + _c("d6")

        pump_score  = round(raw_pump  * 100 / 150, 1)
        crash_score = round(raw_crash * 100 / 150, 1)

        dominant_type  = "PUMP" if pump_score >= crash_score else "CRASH"
        dominant_score = max(pump_score, crash_score)

        return {
            "symbol":         symbol,
            "pump_score":     pump_score,
            "crash_score":    crash_score,
            "dominant_type":  dominant_type,
            "dominant_score": dominant_score,
            "detectors":      results,
            "ticker":         ticker,
            "anomaly":        anomaly,
            "analyzed_at":    datetime.now(timezone.utc).isoformat(),
        }


# ══════════════════════════════════════════════════════════════════════════════
# AlertDispatcher — formata e envia alertas Telegram
# ══════════════════════════════════════════════════════════════════════════════
class AlertDispatcher:
    PUMP_TIERS  = {"CRÍTICO": 85, "ALTO": 72, "MÉDIO": 60}
    CRASH_TIERS = {"CRÍTICO": 85, "ALTO": 72, "MÉDIO": 60}
    ACCEL_MIN   = 22    # alerta por aceleração — threshold mínimo
    COOLDOWNS   = {"CRÍTICO": 10, "ALTO": 20, "MÉDIO": 35}   # minutos

    def __init__(self, memory: MarketMemory):
        self.memory       = memory
        self._alerts_sent = 0   # contador cumulativo da instância

    @property
    def alerts_sent(self) -> int:
        return self._alerts_sent

    def _get_tier(self, score: float, tiers: dict):
        """Retorna o primeiro tier cujo threshold <= score, ou None."""
        for tier, threshold in tiers.items():
            if score >= threshold:
                return tier
        return None

    def should_alert(self, symbol: str, result: dict,
                     acceleration: float, streak: int = 0) -> tuple:
        """
        Retorna (bool, tier, motivo).

        C1 — Lógica corrigida:
          - Score >= 60 (tier direto): alerta imediato via score_threshold.
          - Score < 60 (tier None):    aceleração SÓ dispara se dom_score >= 45
                                       AND streak >= 3 AND acceleration >= 22.
          Elimina alertas espúrios (ex: score 26 + accel 26 não dispara mais).
        """
        dominant_type  = result["dominant_type"]
        dominant_score = result["dominant_score"]
        tiers          = self.PUMP_TIERS if dominant_type == "PUMP" else self.CRASH_TIERS
        tier           = self._get_tier(dominant_score, tiers)

        if tier is not None:
            motivo = "score_threshold"
        elif dominant_score >= 45 and streak >= 3 and acceleration >= 22:
            tier   = "MÉDIO"
            motivo = "acceleration"
        else:
            return False, None, None

        if self.memory.is_in_cooldown(symbol, self.COOLDOWNS[tier]):
            return False, None, None

        return True, tier, motivo

    def _format_message(self, symbol: str, result: dict, tier: str,
                        acceleration: float, streak: int) -> str:
        """
        Formato Telegram rico: header, separador, DNA, níveis de trade
        com percentuais, detectores, linha de aceleração e nota tática.
        """
        dominant_type  = result["dominant_type"]
        dominant_score = result["dominant_score"]
        ticker         = result["ticker"]
        dets           = result["detectors"]
        is_pump        = dominant_type == "PUMP"

        price = float(ticker.get("lastPrice", 0) or 0)
        rfr   = float(ticker.get("riseFallRate", 0) or 0)

        dna      = _get_dna_label(dets, dominant_type)
        levels   = calculate_trade_levels(price, dominant_type, tier)
        prob     = levels["prob_pct"]
        move_pct = levels["move_pct"]
        tactical = _TACTICAL_NOTES.get(dominant_type, {}).get(tier, "")

        header_emoji  = "🚀" if is_pump else "⚠️"
        price_emoji   = "📈" if is_pump else "📉"

        def _s(det_key: str) -> int:
            """Score de um detector para o tipo dominante."""
            d = dets.get(det_key, {})
            if "score" in d:
                return int(d["score"])
            key = "pump" if is_pump else "crash"
            v = d.get(key, 0)
            return int(v) if isinstance(v, (int, float)) else 0

        def _fmt_p(p: float) -> str:
            """Formata preço com precisão adequada."""
            if p >= 1000:
                return f"{p:,.2f}"
            elif p >= 1:
                return f"{p:,.4f}"
            return f"{p:.6f}"

        def _pct(base: float, target: float) -> str:
            """Percentual relativo ao entry."""
            if base == 0:
                return "0.00%"
            diff = (target - base) / base * 100
            return f"{diff:+.2f}%"

        entry = levels['entry']
        stop  = levels['stop']
        tp1   = levels['tp1']
        tp2   = levels['tp2']
        tp3   = levels['tp3']

        lines = [
            f"{header_emoji} {dominant_type} SETUP — {symbol}",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"Tier: {tier} · Score: {dominant_score:.0f}/100",
            f"DNA: {dna}",
            "",
            "💰 NÍVEIS DE TRADE",
            f"Entry:  ${_fmt_p(entry)}",
            f"Stop:   ${_fmt_p(stop)} ({_pct(entry, stop)})",
            f"TP1:    ${_fmt_p(tp1)} ({_pct(entry, tp1)})",
            f"TP2:    ${_fmt_p(tp2)} ({_pct(entry, tp2)})",
            f"TP3:    ${_fmt_p(tp3)} ({_pct(entry, tp3)})",
            f"Move:   ~{move_pct:.1f}% · Prob: {prob}%",
            "",
            "📊 DETECTORES (x/25)",
            f"Funding: {_s('d1')}  OI Accel: {_s('d2')}",
            f"Volume:  {_s('d3')}  CVD:      {_s('d4')}",
            f"Liquidez:{_s('d5')}  Compressão:{_s('d6')}",
            "",
            f"{price_emoji} Preço: ${_fmt_p(price)} · Var24h: {rfr * 100:+.2f}%",
        ]

        if acceleration >= 15:
            lines.append(
                f"⚡ Aceleração: +{acceleration:.0f} pts · Streak: {streak} scans"
            )

        lines += ["", f"💡 {tactical}"]
        return "\n".join(lines)

    def dispatch(self, symbol: str, result: dict) -> bool:
        dominant_type = result["dominant_type"]
        score_key     = "pump_score" if dominant_type == "PUMP" else "crash_score"

        acceleration = self.memory.get_acceleration(symbol, score_key)
        streak       = self.memory.get_streak(symbol, score_key, 52.0)

        should, tier, motivo = self.should_alert(symbol, result, acceleration, streak)
        if not should:
            return False

        message = self._format_message(symbol, result, tier, acceleration, streak)

        try:
            from alerts import send_message
            ok = send_message(message)
        except Exception as e:
            logger.error(f"[ALERT] Erro send_message: {e}")
            ok = False

        if ok:
            self.memory.set_cooldown(symbol)
            self._alerts_sent += 1
            logger.info(
                f"[ALERT] {tier} {dominant_type} — {symbol} "
                f"score={result['dominant_score']:.1f} motivo={motivo}"
            )

        return ok


# ══════════════════════════════════════════════════════════════════════════════
# FullMarketScanner — Orquestrador
# ══════════════════════════════════════════════════════════════════════════════
class FullMarketScanner:
    """
    Interface pública.
    run_full_scan()      → executa um ciclo completo
    get_current_status() → lê o último JSON salvo
    """

    def __init__(self):
        self.memory     = MarketMemory()
        self.triage     = QuickTriageScanner(self.memory)
        self.engine     = DeepAnalysisEngine(self.memory)
        self.dispatcher = AlertDispatcher(self.memory)

    def run_full_scan(self) -> dict:
        """
        1. Triagem (Estágio 1)
        2. Análise profunda em paralelo (Estágio 2)
        3. memory.record + dispatcher.dispatch para cada resultado
        4. Salva dashboard/full_market_scan.json
        5. Retorna o dict salvo
        """
        t0            = time.time()
        alerts_before = self.dispatcher.alerts_sent

        # Estágio 1
        candidates   = self.triage.run()
        stage1_count = len(candidates)
        logger.info(f"[FMS] Triagem: {stage1_count} candidatos")

        # Estágio 2 — análise em paralelo
        results = []

        def _analyze_one(candidate: dict) -> dict:
            symbol  = candidate["symbol"]
            result  = self.engine.analyze(candidate)
            vol_usd  = candidate["anomaly"].get("volume_usd", 0.0)
            hold_vol = float(candidate["ticker"].get("holdVol", 0) or 0)
            self.memory.record(
                symbol,
                result["pump_score"],
                result["crash_score"],
                hold_vol,
                vol_usd,
            )
            self.dispatcher.dispatch(symbol, result)
            return result

        with ThreadPoolExecutor(max_workers=12) as ex:
            futures_map = {ex.submit(_analyze_one, c): c["symbol"] for c in candidates}
            for fut in as_completed(futures_map):
                sym = futures_map[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    logger.error(f"[FMS] Erro analisando {sym}: {e}")

        def _slim(r: dict) -> dict:
            """Versão enxuta sem detectors para o JSON de saída.
            C4: inclui levels e dna para exibição no dashboard."""
            price    = float(r["ticker"].get("lastPrice", 0) or 0)
            dom_type = r["dominant_type"]
            dom_score = r["dominant_score"]
            # Determina tier para cálculo de levels
            tiers = (AlertDispatcher.PUMP_TIERS
                     if dom_type == "PUMP"
                     else AlertDispatcher.CRASH_TIERS)
            tier = "MÉDIO"
            for t, threshold in tiers.items():
                if dom_score >= threshold:
                    tier = t
                    break
            return {
                "symbol":        r["symbol"],
                "pump_score":    r["pump_score"],
                "crash_score":   r["crash_score"],
                "dominant_type": dom_type,
                "last_price":    price,
                "funding_rate":  float(r["ticker"].get("fundingRate", 0) or 0),
                "rise_fall":     float(r["ticker"].get("riseFallRate", 0) or 0),
                "analyzed_at":   r["analyzed_at"],
                "levels":        calculate_trade_levels(price, dom_type, tier),
                "dna":           _get_dna_label(r["detectors"], dom_type),
            }

        top_pump  = sorted(results, key=lambda x: x["pump_score"],  reverse=True)[:10]
        top_crash = sorted(results, key=lambda x: x["crash_score"], reverse=True)[:10]

        elapsed          = time.time() - t0
        contracts_scanned = self.triage._last_total or stage1_count
        alerts_this_scan  = self.dispatcher.alerts_sent - alerts_before

        scan_result = {
            "scan_ts":            datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds":    round(elapsed, 1),
            "contracts_scanned":  contracts_scanned,
            "candidates_stage1":  stage1_count,
            "candidates_stage2":  len(results),
            "alerts_sent":        alerts_this_scan,
            "top_pump":           [_slim(r) for r in top_pump],
            "top_crash":          [_slim(r) for r in top_crash],
        }

        try:
            FMS_SCAN_FILE.write_text(json.dumps(scan_result, indent=2))
        except Exception as e:
            logger.error(f"[FMS] Erro ao salvar JSON: {e}")

        logger.info(
            f"[FMS] {elapsed:.1f}s — {contracts_scanned} contratos · "
            f"{len(results)} candidatos · {alerts_this_scan} alertas"
        )
        return scan_result

    def get_current_status(self) -> dict:
        """Lê dashboard/full_market_scan.json. Retorna {} se não existir."""
        try:
            if FMS_SCAN_FILE.exists():
                return json.loads(FMS_SCAN_FILE.read_text())
        except Exception as e:
            logger.error(f"[FMS] Erro ao ler status: {e}")
        return {}
