"""
arb_opportunity_engine.py — Engine de Arbitragem de Funding Rate Cross-Exchange

Consome o output bruto do ExchangeManager.get_funding_arbitrage_opportunities()
e enriquece cada oportunidade com:
  - Confidence score 0-100 (4 dimensões + blue chip boost)
  - Net spread após taxas de execução
  - P&L projetado por $10K capital (diário/semanal/mensal)
  - Ciclos de breakeven
  - Tipo de arbitragem (cross_exchange, cash_carry, reverse)
  - Tier (HOT, BLUE_CHIP, NORMAL)
  - Ranking score combinado

Cache 30s para não sobrecarregar o ExchangeManager.
"""

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Blue Chip symbols ─────────────────────────────────────────────────────────
# Importar do crash_scoring_engine + adicionar BTC
BLUE_CHIPS = {
    "BTCUSDT",  # adicionado — sempre blue chip para arb
    "ETHUSDT", "SOLUSDT", "SUIUSDT", "LINKUSDT",
    "AVAXUSDT", "DOTUSDT", "ADAUSDT", "MATICUSDT",
    "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
    "ATOMUSDT", "INJUSDT", "TIAUSDT", "SEIUSDT",
    "DOGEUSDT", "XRPUSDT", "BNBUSDT", "LTCUSDT",
    "PEPEUSDT", "WIFUSDT", "BONKUSDT", "JUPUSDT",
    "ENAUSDT", "RENDERUSDT", "FETUSDT", "ONDOUSDT",
}

# ── Exchange fee table (maker/taker) ──────────────────────────────────────────
# Baseado em tiers públicos (sem API key, sem volume discount)
EXCHANGE_FEES = {
    "mexc":    {"maker": 0.0000, "taker": 0.0002},   # 0.00% maker, 0.02% taker
    "binance": {"maker": 0.0002, "taker": 0.0004},   # 0.02% maker, 0.04% taker
    "bybit":   {"maker": 0.0001, "taker": 0.0006},   # 0.01% maker, 0.06% taker
    "okx":     {"maker": 0.0002, "taker": 0.0005},   # 0.02% maker, 0.05% taker
    "gateio":  {"maker": 0.0000, "taker": 0.0005},   # 0.00% maker, 0.05% taker
}
# Fallback para exchanges desconhecidas
_DEFAULT_FEES = {"maker": 0.0002, "taker": 0.0004}


class ArbOpportunityEngine:
    """
    Enriquece oportunidades de arb cross-exchange com scoring e projeções financeiras.
    Cache 30s independente do ExchangeManager.
    """

    def __init__(self, ex_mgr):
        self._ex_mgr = ex_mgr
        self._opps_cache: list[dict] = []
        self._opps_cache_ts: float = 0.0
        self._cache_ttl: float = 30.0

        # Histórico de observações por símbolo — últimas 24h
        # {symbol: [(ts, spread_annual_pct), ...]}
        self._funding_history: dict[str, list[tuple[float, float]]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def get_opportunities(
        self,
        min_spread_annual: float = 20.0,
        min_score: int = 0,
        top_n: int = 50,
    ) -> list[dict]:
        """
        Retorna oportunidades enriquecidas, ordenadas por ranking_score DESC.
        Cache 30s.
        """
        now = time.time()
        if now - self._opps_cache_ts < self._cache_ttl and self._opps_cache:
            filtered = [
                o for o in self._opps_cache
                if o["spread_annual_pct"] >= min_spread_annual
                and o["confidence"] >= min_score
            ]
            return filtered[:top_n]

        try:
            raw_opps = self._ex_mgr.get_funding_arbitrage_opportunities(
                min_spread_annual=0.0
            )
            unified = self._ex_mgr.fetch_all_tickers_unified()
        except Exception as e:
            logger.error(f"[ArbEngine] Erro ao buscar dados: {e}")
            return self._opps_cache[:top_n] if self._opps_cache else []

        enriched = []
        for raw in raw_opps:
            try:
                opp = self._enrich(raw, unified)
                enriched.append(opp)
                self._update_history(opp)
            except Exception as e:
                logger.debug(
                    f"[ArbEngine] Erro ao enriquecer {raw.get('symbol', '?')}: {e}"
                )

        # Ordenar por ranking_score DESC
        enriched.sort(key=lambda x: x["ranking_score"], reverse=True)

        self._opps_cache = enriched
        self._opps_cache_ts = now

        hot = sum(1 for o in enriched if o["tier"] == "HOT")
        logger.info(
            f"[ArbEngine] {len(enriched)} oportunidades enriquecidas "
            f"({hot} HOT, {len(enriched) - hot} normais)"
        )

        filtered = [
            o for o in enriched
            if o["spread_annual_pct"] >= min_spread_annual
            and o["confidence"] >= min_score
        ]
        return filtered[:top_n]

    def get_aggregate_stats(self) -> dict:
        """Stats agregadas para a barra de resumo do dashboard."""
        opps = self.get_opportunities()
        if not opps:
            return {
                "total_opportunities": 0,
                "hot_count": 0,
                "avg_apr": 0.0,
                "avg_confidence": 0.0,
                "best_apr": 0.0,
                "best_symbol": "—",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        hot = [o for o in opps if o["tier"] == "HOT"]
        profitable = [o for o in opps if o["is_profitable"]]
        return {
            "total_opportunities": len(opps),
            "profitable_count":    len(profitable),
            "hot_count":           len(hot),
            "avg_apr":             round(
                sum(o["net_apr"] for o in opps) / len(opps), 2
            ),
            "avg_confidence":      round(
                sum(o["confidence"] for o in opps) / len(opps), 1
            ),
            "best_apr":            opps[0]["net_apr"] if opps else 0.0,
            "best_symbol":         opps[0]["symbol"] if opps else "—",
            "timestamp":           datetime.now(timezone.utc).isoformat(),
        }

    def get_heatmap(self) -> dict:
        """
        Matriz de pares cross-exchange + stats globais para o heatmap.
        """
        opps = self.get_opportunities()
        exchanges = ["mexc", "binance", "bybit", "okx", "gateio"]

        # Construir matriz de todos os pares
        matrix: dict[str, dict] = {}
        for i, ex1 in enumerate(exchanges):
            for j, ex2 in enumerate(exchanges):
                if i >= j:
                    continue
                key = f"{ex1}_vs_{ex2}"
                pair_opps = [
                    o for o in opps
                    if {o["long_exchange"], o["short_exchange"]} == {ex1, ex2}
                ]
                matrix[key] = {
                    "count":       len(pair_opps),
                    "avg_apr":     round(
                        sum(o["net_apr"] for o in pair_opps) / len(pair_opps), 2
                    ) if pair_opps else 0.0,
                    "best_symbol": pair_opps[0]["symbol"] if pair_opps else "—",
                    "best_apr":    pair_opps[0]["net_apr"] if pair_opps else 0.0,
                }

        return {
            "exchanges": exchanges,
            "matrix":    matrix,
            "stats":     self.get_aggregate_stats(),
        }

    # ── Enrich ────────────────────────────────────────────────────────────────

    def _enrich(self, raw: dict, unified: dict) -> dict:
        """Adiciona campos de scoring, fees e P&L a uma oportunidade bruta."""
        symbol         = raw["symbol"]
        long_ex        = raw["long_exchange"]
        short_ex       = raw["short_exchange"]
        spread_8h      = raw["spread_8h"]          # decimal, ex: 0.002 = 0.2%
        spread_annual  = raw["spread_annual_pct"]  # em %, ex: 219.0
        long_fr        = raw["long_funding_8h"]    # decimal
        short_fr       = raw["short_funding_8h"]   # decimal
        price_spread   = raw["price_spread_pct"]   # em %
        is_blue        = symbol in BLUE_CHIPS

        # ── OI e Volume dos tickers envolvidos ────────────────────────────────
        tickers      = unified.get(symbol, [])
        long_ticker  = next((t for t in tickers if t.exchange == long_ex), None)
        short_ticker = next((t for t in tickers if t.exchange == short_ex), None)

        oi_usd = max(
            getattr(long_ticker,  "open_interest_usd", 0.0) or 0.0,
            getattr(short_ticker, "open_interest_usd", 0.0) or 0.0,
        )
        vol_usd = (
            (getattr(long_ticker,  "volume_24h_usd", 0.0) or 0.0) +
            (getattr(short_ticker, "volume_24h_usd", 0.0) or 0.0)
        )

        # ── Taxas de execução ─────────────────────────────────────────────────
        long_fees  = EXCHANGE_FEES.get(long_ex,  _DEFAULT_FEES)
        short_fees = EXCHANGE_FEES.get(short_ex, _DEFAULT_FEES)

        # 2 entradas (taker) + 2 saídas (maker) por ciclo completo de 8h
        total_exec_cost = (
            long_fees["taker"]  + long_fees["maker"] +
            short_fees["taker"] + short_fees["maker"]
        )

        net_spread_8h = spread_8h - total_exec_cost
        net_apr       = net_spread_8h * 3 * 365 * 100   # anualizado

        if net_spread_8h > 0:
            breakeven_cycles = round(total_exec_cost / net_spread_8h, 2)
        else:
            breakeven_cycles = 999.0

        is_profitable = (breakeven_cycles < 3) and (net_spread_8h > 0)

        pnl_daily_10k   = round(net_spread_8h * 3 * 10_000, 2)
        pnl_weekly_10k  = round(pnl_daily_10k * 7, 2)
        pnl_monthly_10k = round(pnl_daily_10k * 30, 2)

        # ── Tipo de arbitragem ────────────────────────────────────────────────
        if long_fr < 0 and short_fr > 0:
            arb_type = "cross_exchange"   # lados opostos — spread máximo
        elif short_fr > 0.001:
            arb_type = "cash_carry"       # carry clássico
        elif long_fr < -0.001:
            arb_type = "reverse"          # reverse carry
        else:
            arb_type = "cross_exchange"

        # ── Confidence score ──────────────────────────────────────────────────
        confidence = self._calculate_confidence(
            symbol, raw, oi_usd, vol_usd, is_blue
        )

        # ── Tier ──────────────────────────────────────────────────────────────
        if spread_annual >= 300 and confidence >= 70:
            tier = "HOT"
        elif is_blue and confidence >= 50:
            tier = "BLUE_CHIP"
        else:
            tier = "NORMAL"

        # ── Ranking score ─────────────────────────────────────────────────────
        # Penaliza oportunidades em exchanges sem OI disponível (OI = 0)
        oi_factor = min(1.0, oi_usd / 5_000_000) if oi_usd > 0 else 0.1
        ranking_score = net_apr * (confidence / 100.0) * oi_factor

        return {
            # ── Passthrough raw ──
            "symbol":              symbol,
            "long_exchange":       long_ex,
            "short_exchange":      short_ex,
            "long_funding_8h":     round(long_fr * 100, 6),    # em %
            "short_funding_8h":    round(short_fr * 100, 6),   # em %
            "spread_8h":           round(spread_8h * 100, 6),  # em %
            "spread_annual_pct":   round(spread_annual, 2),
            "long_price":          raw["long_price"],
            "short_price":         raw["short_price"],
            "price_spread_pct":    round(price_spread, 4),
            # ── Calculados ──
            "oi_usd":              round(oi_usd, 0),
            "vol_usd_24h":         round(vol_usd, 0),
            "is_blue_chip":        is_blue,
            "arb_type":            arb_type,
            "total_exec_cost_pct": round(total_exec_cost * 100, 6),
            "net_spread_8h_pct":   round(net_spread_8h * 100, 6),
            "net_apr":             round(net_apr, 2),
            "breakeven_cycles":    breakeven_cycles,
            "is_profitable":       is_profitable,
            "pnl_daily_10k":       pnl_daily_10k,
            "pnl_weekly_10k":      pnl_weekly_10k,
            "pnl_monthly_10k":     pnl_monthly_10k,
            "confidence":          confidence,
            "tier":                tier,
            "ranking_score":       round(ranking_score, 4),
        }

    # ── Confidence scoring ────────────────────────────────────────────────────

    def _calculate_confidence(
        self,
        symbol: str,
        raw: dict,
        oi_usd: float,
        vol_usd: float,
        is_blue: bool,
    ) -> int:
        """
        Confidence score 0-100 em 4 dimensões + blue chip boost.
          Magnitude    0-30  (quão grande é o spread)
          Liquidez     0-30  (volume + OI, 15 cada)
          Qualidade    0-25  (price_spread — facilidade de execução)
          Consistência 0-15  (histórico de observações)
          Blue Chip    +5    (símbolo tier-1)
        """
        spread_annual = raw["spread_annual_pct"]
        price_spread  = raw["price_spread_pct"]

        # 1. Magnitude (0-30)
        if   spread_annual >= 500: mag = 30
        elif spread_annual >= 200: mag = 24
        elif spread_annual >= 100: mag = 18
        elif spread_annual >= 50:  mag = 12
        elif spread_annual >= 20:  mag = 6
        else:                      mag = 0

        # 2. Liquidez — volume (0-15) + OI (0-15)
        if   vol_usd >= 50_000_000: vol_score = 15
        elif vol_usd >= 10_000_000: vol_score = 10
        elif vol_usd >= 1_000_000:  vol_score = 5
        else:                       vol_score = 0

        if   oi_usd >= 20_000_000:  oi_score = 15
        elif oi_usd >= 5_000_000:   oi_score = 10
        elif oi_usd >= 1_000_000:   oi_score = 5
        else:                       oi_score = 0

        liq = vol_score + oi_score

        # 3. Qualidade de execução / price_spread (0-25)
        if   price_spread <= 0.05: qual = 25
        elif price_spread <= 0.10: qual = 20
        elif price_spread <= 0.20: qual = 15
        elif price_spread <= 0.50: qual = 8
        else:                      qual = 2

        # 4. Consistência / Histórico (0-15)
        history = self._funding_history.get(symbol, [])
        obs = len(history)
        if   obs >= 8: cons = 15
        elif obs >= 4: cons = 10
        elif obs >= 2: cons = 5
        else:          cons = 0

        # Blue chip boost
        boost = 5 if is_blue else 0

        total = mag + liq + qual + cons + boost
        return min(100, total)

    # ── History ───────────────────────────────────────────────────────────────

    def _update_history(self, enriched: dict):
        """Mantém histórico rolling de 24h por símbolo para Consistency score."""
        now    = time.time()
        cutoff = now - 86_400   # 24h em segundos

        symbol = enriched["symbol"]
        entry  = (now, enriched["spread_annual_pct"])

        hist = self._funding_history.setdefault(symbol, [])
        hist.append(entry)

        # Remover entradas antigas
        self._funding_history[symbol] = [
            (ts, val) for ts, val in hist if ts >= cutoff
        ]
