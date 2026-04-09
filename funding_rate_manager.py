"""
funding_rate_manager.py — Camada de confirmação por funding rate

Filtra e pontua trades com base no funding rate atual e histórico.
Nunca substitui o sinal primário — apenas valida e ajusta tamanho.

Dependências: requests, csv (stdlib apenas)
"""

import csv
import time
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import requests

logger = logging.getLogger(__name__)

# ── Thresholds (ajuste sem tocar no restante do código) ───────────────────────
FUNDING_BLOCK_LONG_ABOVE    =  0.0007   # > +0.07% → bloqueia LONG
FUNDING_BLOCK_SHORT_BELOW   = -0.0003   # < -0.03% → bloqueia SHORT
FUNDING_CONFIRM_LONG_BELOW  = -0.0003   # < -0.03% → confirma LONG (+2)
FUNDING_CONFIRM_SHORT_ABOVE =  0.0003   # > +0.03% → confirma SHORT (+2)
FUNDING_NEUTRAL_RANGE       =  0.0001   # ±0.01%   → neutro absoluto
FUNDING_HISTORY_PERIODS     =  8        # períodos no histórico
FUNDING_CACHE_SECONDS       = 30        # TTL do cache em segundos

FUNDING_SIZE_SCALE = {
     2: 1.0,   # 100% do tamanho planejado
     1: 0.8,   # 80%
     0: 0.6,   # 60%
    -1: 0.0,   # bloqueado
    -2: 0.0,   # bloqueado + alerta
}

# ── Tipos ─────────────────────────────────────────────────────────────────────
Exchange  = Literal["mexc", "gateio"]
Direction = Literal["LONG", "SHORT"]

LOG_PATH = Path(__file__).parent / "logs" / "funding_decisions.csv"
LOG_FIELDS = [
    "timestamp", "exchange", "symbol", "direction",
    "funding_atual", "funding_previsto", "tendencia",
    "score", "decisao", "tamanho_ajustado", "motivo",
]


# ─────────────────────────────────────────────────────────────────────────────
# Cache thread-safe
# ─────────────────────────────────────────────────────────────────────────────
class _Cache:
    def __init__(self, ttl: int):
        self._ttl   = ttl
        self._store: dict[str, tuple[float, object]] = {}
        self._lock  = threading.Lock()

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if entry and (time.monotonic() - entry[0]) < self._ttl:
                return entry[1]
        return None

    def set(self, key: str, value):
        with self._lock:
            self._store[key] = (time.monotonic(), value)


# ─────────────────────────────────────────────────────────────────────────────
# Adaptadores REST por exchange
# ─────────────────────────────────────────────────────────────────────────────
def _symbol_mexc(symbol: str) -> str:
    """BTC/USDT → BTC_USDT  (formato MEXC contract)"""
    return symbol.replace("/", "_").replace(":USDT", "").upper()


def _symbol_gateio(symbol: str) -> str:
    """BTC/USDT → BTC_USDT  (formato Gate.io contract)"""
    return symbol.replace("/", "_").replace(":USDT", "").upper()


def _retry_get(url: str, params: dict = None, max_tries: int = 3) -> dict:
    """GET com retry exponencial. Lança RuntimeError após esgotar tentativas."""
    delay = 1.0
    last_exc: Exception = RuntimeError("nenhuma tentativa realizada")
    for attempt in range(1, max_tries + 1):
        try:
            resp = requests.get(url, params=params, timeout=8)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            logger.warning(f"[funding] tentativa {attempt}/{max_tries} falhou: {exc}")
            if attempt < max_tries:
                time.sleep(delay)
                delay *= 2
    raise RuntimeError(f"[funding] todas tentativas falharam: {last_exc}") from last_exc


def _fetch_mexc(symbol: str) -> dict:
    """
    Retorna:
        {
            "current":   float,   # funding rate atual (decimal, ex: 0.0001)
            "predicted": float,   # próximo funding rate
            "next_ts":   int,     # timestamp ms do próximo pagamento
            "history":   [float], # últimos N funding rates (mais recente por último)
        }
    """
    sym = _symbol_mexc(symbol)
    base = "https://contract.mexc.com"

    # Funding atual
    data_cur = _retry_get(f"{base}/api/v1/contract/funding_rate/{sym}")
    cur  = float(data_cur["data"]["fundingRate"])
    pred = float(data_cur["data"].get("nextFundingRate", cur))
    nts  = int(data_cur["data"].get("nextSettleTime", 0))

    # Histórico (page_size=FUNDING_HISTORY_PERIODS)
    data_hist = _retry_get(
        f"{base}/api/v1/contract/funding_rate/history",
        params={"symbol": sym, "page_num": 1, "page_size": FUNDING_HISTORY_PERIODS},
    )
    history_raw = data_hist.get("data", {}).get("resultList", [])
    history = [float(r["fundingRate"]) for r in reversed(history_raw)]  # cronológico

    return {"current": cur, "predicted": pred, "next_ts": nts, "history": history}


def _fetch_gateio(symbol: str) -> dict:
    """Gate.io Futures USDT — mesmo formato de retorno."""
    sym  = _symbol_gateio(symbol)
    base = "https://api.gateio.ws/api/v4"

    # Funding atual via contrato
    contract = _retry_get(f"{base}/futures/usdt/contracts/{sym}")
    cur  = float(contract.get("funding_rate", 0))
    pred = float(contract.get("funding_rate_indicative", cur))
    nts  = int(float(contract.get("funding_next_apply", 0)) * 1000)  # s → ms

    # Histórico
    hist_raw = _retry_get(
        f"{base}/futures/usdt/funding_rate",
        params={"contract": sym, "limit": FUNDING_HISTORY_PERIODS},
    )
    history = [float(r["r"]) for r in reversed(hist_raw)]  # cronológico

    return {"current": cur, "predicted": pred, "next_ts": nts, "history": history}


_FETCHERS = {"mexc": _fetch_mexc, "gateio": _fetch_gateio}


# ─────────────────────────────────────────────────────────────────────────────
# Score e tendência
# ─────────────────────────────────────────────────────────────────────────────
def score_funding(funding_rate: float, direction: Direction) -> int:
    """
    Retorna score de -2 a +2.
    -2 / -1 → bloqueado
     0      → neutro
    +1 / +2 → confirmado
    """
    fr = funding_rate

    if direction == "LONG":
        if fr < FUNDING_CONFIRM_LONG_BELOW:                                return  2
        if FUNDING_CONFIRM_LONG_BELOW <= fr < -FUNDING_NEUTRAL_RANGE:      return  1
        if -FUNDING_NEUTRAL_RANGE    <= fr <= FUNDING_NEUTRAL_RANGE * 3:   return  0
        if FUNDING_NEUTRAL_RANGE * 3 <  fr <= FUNDING_BLOCK_LONG_ABOVE:    return -1
        return -2  # fr > FUNDING_BLOCK_LONG_ABOVE

    else:  # SHORT
        if fr > FUNDING_CONFIRM_SHORT_ABOVE:                               return  2
        if FUNDING_NEUTRAL_RANGE    <  fr <= FUNDING_CONFIRM_SHORT_ABOVE:  return  1
        if -FUNDING_NEUTRAL_RANGE   <= fr <= FUNDING_NEUTRAL_RANGE:        return  0
        if FUNDING_BLOCK_SHORT_BELOW <= fr < -FUNDING_NEUTRAL_RANGE:       return -1
        return -2  # fr < FUNDING_BLOCK_SHORT_BELOW


def detect_trend(history: list[float]) -> tuple[str, int]:
    """
    Analisa histórico cronológico e retorna (tendencia, bonus).

    tendencia:
        "escalando_positivo"  → funding subindo 3+ períodos consecutivos
        "escalando_negativo"  → funding caindo 3+ períodos consecutivos
        "revertendo"          → mudança de direção após extremo
        "neutro"              → sem padrão claro

    bonus: +1 se revertendo (sinal contrário ao extremo anterior), 0 caso contrário
    """
    if len(history) < 3:
        return "neutro", 0

    diffs = [history[i + 1] - history[i] for i in range(len(history) - 1)]

    # 3+ períodos consecutivos
    subindo   = all(d > 0 for d in diffs[-3:])
    descendo  = all(d < 0 for d in diffs[-3:])

    if subindo:
        return "escalando_positivo", 0
    if descendo:
        return "escalando_negativo", 0

    # Reversão: máximo ou mínimo no penúltimo, mudança de direção no último
    extreme_max = max(history[:-1]) == history[-2] and history[-1] < history[-2]
    extreme_min = min(history[:-1]) == history[-2] and history[-1] > history[-2]

    if extreme_max or extreme_min:
        return "revertendo", 1

    return "neutro", 0


# ─────────────────────────────────────────────────────────────────────────────
# FundingRateManager — ponto de entrada único
# ─────────────────────────────────────────────────────────────────────────────
class FundingRateManager:
    """
    Uso:
        manager = FundingRateManager()
        ok, score, reason = manager.validate_trade("BTC/USDT", "LONG", "mexc")
        size = manager.adjusted_size(base_size, score)
    """

    def __init__(self):
        self._cache = _Cache(ttl=FUNDING_CACHE_SECONDS)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not LOG_PATH.exists():
            with open(LOG_PATH, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=LOG_FIELDS).writeheader()

    # ── Dados brutos ──────────────────────────────────────────────────────────
    def get_funding_rate(self, symbol: str, exchange: Exchange) -> dict:
        """
        Retorna dados de funding com cache de FUNDING_CACHE_SECONDS.
        Lança RuntimeError se não conseguir após retries.
        """
        key = f"{exchange}:{symbol}"
        cached = self._cache.get(key)
        if cached:
            return cached

        fetcher = _FETCHERS.get(exchange)
        if not fetcher:
            raise ValueError(f"Exchange não suportada: {exchange}")

        data = fetcher(symbol)
        self._cache.set(key, data)
        logger.debug(f"[funding] {exchange} {symbol} → current={data['current']:.5%}")
        return data

    # ── Validação principal ───────────────────────────────────────────────────
    def validate_trade(
        self,
        symbol: str,
        direction: Direction,
        exchange: Exchange,
    ) -> tuple[bool, int, str]:
        """
        Valida se o funding rate confirma o trade.

        Retorna:
            (should_trade: bool, score: int, reason: str)

        score >= 0  → trade permitido
        score <  0  → trade bloqueado
        """
        try:
            data = self.get_funding_rate(symbol, exchange)
        except Exception as exc:
            reason = f"funding indisponível — {exc}"
            logger.warning(f"[funding] {symbol} {direction}: {reason}")
            self._log_decision(
                exchange=exchange, symbol=symbol, direction=direction,
                funding_atual=None, funding_previsto=None,
                tendencia="erro", score=0,
                decisao="PERMITIDO_SEM_DADOS", tamanho_ajustado=0.6,
                motivo=reason,
            )
            # Falha silenciosa: permite o trade com tamanho conservador (score 0)
            return True, 0, reason

        fr      = data["current"]
        fr_pred = data["predicted"]
        history = data["history"]

        base_score           = score_funding(fr, direction)
        tendencia, trend_bonus = detect_trend(history)

        # Bônus de reversão só ajuda, nunca piora um score negativo
        final_score = base_score + (trend_bonus if base_score >= 0 else 0)
        final_score = max(-2, min(2, final_score))  # clamp [-2, +2]

        # Decisão
        if final_score >= 0:
            should_trade = True
            tag = "funding_confirmado" if final_score >= 1 else "funding_neutro"
            reason = (
                f"{tag} | fr={fr:.5%} | previsto={fr_pred:.5%} | "
                f"tendencia={tendencia} | score={final_score:+d}"
            )
        else:
            should_trade = False
            alerta = " ⚠ ALERTA EXTREMO" if final_score == -2 else ""
            reason = (
                f"BLOQUEADO{alerta} | fr={fr:.5%} | previsto={fr_pred:.5%} | "
                f"tendencia={tendencia} | score={final_score:+d}"
            )
            level = logging.WARNING if final_score == -1 else logging.ERROR
            logger.log(level, f"[funding] {exchange} {symbol} {direction}: {reason}")

        size_mult = FUNDING_SIZE_SCALE.get(final_score, 0.0)
        decisao   = "LIBERADO" if should_trade else "BLOQUEADO"

        self._log_decision(
            exchange=exchange, symbol=symbol, direction=direction,
            funding_atual=fr, funding_previsto=fr_pred,
            tendencia=tendencia, score=final_score,
            decisao=decisao, tamanho_ajustado=size_mult,
            motivo=reason,
        )

        return should_trade, final_score, reason

    # ── Validação a partir de dados já buscados (sem nova chamada de API) ─────
    def validate_from_deriv_data(
        self,
        deriv_data: dict,
        direction: Direction,
        exchange: str = "mexc",
    ) -> tuple[bool, int, str]:
        """
        Valida usando o funding rate já presente em derivatives_data.
        Use este método no fluxo principal do bot (onde derivatives.analyze()
        já foi chamado) para evitar chamada duplicada à API.

        O campo "funding_rate_raw" (decimal) tem prioridade.
        Fallback para "funding_rate" (percentual) ÷ 100.

        Exemplo em main.py:
            deriv_data = derivatives.analyze("BTC")
            ok, score, reason = manager.validate_from_deriv_data(deriv_data, "LONG")
        """
        # Extrai valor decimal — funding_rate_raw é inserido pelo derivatives.analyze()
        if "funding_rate_raw" in deriv_data:
            fr = float(deriv_data["funding_rate_raw"])
        elif "funding_rate" in deriv_data:
            fr = float(deriv_data["funding_rate"]) / 100   # % → decimal
        else:
            reason = "funding_rate ausente em deriv_data — trade liberado conservador"
            logger.warning(f"[funding] {reason}")
            self._log_decision(
                exchange=exchange, symbol="?", direction=direction,
                funding_atual=None, funding_previsto=None,
                tendencia="sem_dados", score=0,
                decisao="LIBERADO_SEM_DADOS", tamanho_ajustado=0.6, motivo=reason,
            )
            return True, 0, reason

        base_score = score_funding(fr, direction)

        # Sem histórico via Coinglass: sem bônus de tendência
        final_score = max(-2, min(2, base_score))

        symbol = deriv_data.get("symbol", "BTC/USDT")

        if final_score >= 0:
            should_trade = True
            tag = "funding_confirmado" if final_score >= 1 else "funding_neutro"
            reason = f"{tag} | fr={fr:.5%} | score={final_score:+d} [via deriv_data]"
        else:
            should_trade = False
            alerta = " ⚠ ALERTA EXTREMO" if final_score == -2 else ""
            reason = f"BLOQUEADO{alerta} | fr={fr:.5%} | score={final_score:+d} [via deriv_data]"
            level = logging.WARNING if final_score == -1 else logging.ERROR
            logger.log(level, f"[funding] {exchange} {symbol} {direction}: {reason}")

        size_mult = FUNDING_SIZE_SCALE.get(final_score, 0.0)

        self._log_decision(
            exchange=exchange, symbol=symbol, direction=direction,
            funding_atual=fr, funding_previsto=None,
            tendencia="n/a", score=final_score,
            decisao="LIBERADO" if should_trade else "BLOQUEADO",
            tamanho_ajustado=size_mult, motivo=reason,
        )

        return should_trade, final_score, reason

    # ── Ajuste de tamanho ─────────────────────────────────────────────────────
    def adjusted_size(self, base_size: float, score: int) -> float:
        """
        Aplica o multiplicador do FUNDING_SIZE_SCALE ao tamanho base.

        Exemplo:
            size = manager.adjusted_size(100.0, score=1)  # → 80.0
        """
        mult = FUNDING_SIZE_SCALE.get(score, 0.0)
        return round(base_size * mult, 8)

    # ── Log CSV ───────────────────────────────────────────────────────────────
    def _log_decision(
        self,
        exchange: str,
        symbol: str,
        direction: str,
        funding_atual: Optional[float],
        funding_previsto: Optional[float],
        tendencia: str,
        score: int,
        decisao: str,
        tamanho_ajustado: float,
        motivo: str,
    ):
        row = {
            "timestamp":        datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "exchange":         exchange,
            "symbol":           symbol,
            "direction":        direction,
            "funding_atual":    f"{funding_atual:.6%}" if funding_atual is not None else "N/A",
            "funding_previsto": f"{funding_previsto:.6%}" if funding_previsto is not None else "N/A",
            "tendencia":        tendencia,
            "score":            f"{score:+d}",
            "decisao":          decisao,
            "tamanho_ajustado": f"{tamanho_ajustado:.0%}",
            "motivo":           motivo,
        }
        try:
            with open(LOG_PATH, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=LOG_FIELDS).writerow(row)
        except Exception as exc:
            logger.warning(f"[funding] falha ao gravar log: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Singleton global — importar e reutilizar
# ─────────────────────────────────────────────────────────────────────────────
_manager: Optional[FundingRateManager] = None


def get_manager() -> FundingRateManager:
    """Retorna instância singleton do FundingRateManager."""
    global _manager
    if _manager is None:
        _manager = FundingRateManager()
    return _manager


# Alias de conveniência para uso direto
def validate_trade_funding(
    symbol: str,
    direction: Direction,
    exchange: Exchange = "mexc",
) -> tuple[bool, int, str]:
    """
    Função de conveniência — chame antes de qualquer ordem.

    Exemplo:
        should_trade, score, reason = validate_trade_funding("BTC/USDT", "LONG", "mexc")
        if not should_trade:
            return  # trade bloqueado
        size = get_manager().adjusted_size(planned_size, score)
    """
    return get_manager().validate_trade(symbol, direction, exchange)
