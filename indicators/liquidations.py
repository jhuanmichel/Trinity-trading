"""
indicators/liquidations.py — Dados de Liquidações via Coinglass
"""
import requests
from config import COINGLASS_API_KEY

_CG_V3_BASE = "https://open-api-v3.coinglass.com/api"


def analyze(symbol: str = "BTC"):
    try:
        url = "https://open-api.coinglass.com/public/v2/liquidation_history"
        params = {"symbol": symbol, "timeType": "1", "limit": 1}
        r = requests.get(url, headers={"coinglassSecret": COINGLASS_API_KEY}, params=params, timeout=10)
        data = r.json().get("data", [])
        if not data:
            return {
                "score": 50,
                "summary": "Liquidações: INDISPONÍVEL",
                "heatmap_summary": "",
                "liq_24h_long_usd": 0,
                "liq_24h_short_usd": 0,
                "liq_1h_long_usd": 0,
                "liq_1h_short_usd": 0,
                "liq_signal": "N/A",
                "taker_ratio": "N/A",
                "taker_signal": "N/A",
                "leverage_ratio": "N/A",
                "leverage_signal": "N/A",
                "miner_signal": "N/A",
            }
        latest = data[0]
        long_liq = float(latest.get("longLiquidationUsd", 0)) / 1e6
        short_liq = float(latest.get("shortLiquidationUsd", 0)) / 1e6
        total = long_liq + short_liq
        score = 50
        if short_liq > long_liq and short_liq > 10:
            score += 20
        elif long_liq > short_liq and long_liq > 10:
            score -= 20
        if total < 10:
            score = 50
        score = max(0, min(100, score))

        summary = f"Liquidações: Longs ${long_liq:.1f}M | Shorts ${short_liq:.1f}M | Total ${total:.1f}M"

        return {
            "score": round(score),
            "summary": summary,
            "heatmap_summary": "",
            "long_liquidated_usd": round(long_liq, 2),
            "short_liquidated_usd": round(short_liq, 2),
            "total_liquidated_usd": round(total, 2),
            "liq_24h_long_usd": round(long_liq, 2),
            "liq_24h_short_usd": round(short_liq, 2),
            "liq_1h_long_usd": round(long_liq, 2),
            "liq_1h_short_usd": round(short_liq, 2),
            "liq_signal": "MAIS SHORTS" if short_liq > long_liq else ("MAIS LONGS" if long_liq > short_liq else "EQUILIBRADO"),
            "taker_ratio": "N/A",
            "taker_signal": "N/A",
            "leverage_ratio": "N/A",
            "leverage_signal": "N/A",
            "miner_signal": "N/A",
        }
    except Exception as e:
        return {
            "score": 50,
            "summary": f"Liquidações: INDISPONÍVEL ({e})",
            "heatmap_summary": "",
            "liq_24h_long_usd": 0,
            "liq_24h_short_usd": 0,
            "liq_1h_long_usd": 0,
            "liq_1h_short_usd": 0,
            "liq_signal": "N/A",
            "taker_ratio": "N/A",
            "taker_signal": "N/A",
            "leverage_ratio": "N/A",
            "leverage_signal": "N/A",
            "miner_signal": "N/A",
        }


def get_heatmap(symbol: str = "BTC", timeframe: str = "12h") -> dict:
    """
    Busca heatmap de liquidações via Coinglass API v3.
    Retorna lista de níveis de preço com volumes de long/short liq em $M.

    Returns:
        {"levels": [{"price": float, "long_usd": float, "short_usd": float}, ...]}
    """
    try:
        r = requests.get(
            f"{_CG_V3_BASE}/futures/liquidation/map",
            headers={"CG-API-KEY": COINGLASS_API_KEY},
            params={"symbol": symbol, "timeframe": timeframe},
            timeout=8,
        )
        raw = r.json()
        d = raw.get("data") or {}

        # Suporta variações de chave na resposta da API
        prices = d.get("y")      or d.get("priceList")    or []
        longs  = d.get("longs")  or d.get("longLiqList")  or d.get("longList")  or []
        shorts = d.get("shorts") or d.get("shortLiqList") or d.get("shortList") or []

        if not prices:
            return {"levels": [], "error": "resposta vazia"}

        levels = []
        for i, p in enumerate(prices):
            lv = float(longs[i])  if i < len(longs)  else 0.0
            sv = float(shorts[i]) if i < len(shorts) else 0.0
            if lv + sv > 0:
                levels.append({
                    "price":     round(float(p), 2),
                    "long_usd":  round(lv / 1_000_000, 3),   # USD → $M
                    "short_usd": round(sv / 1_000_000, 3),
                })

        return {"levels": levels}

    except Exception as e:
        return {"levels": [], "error": str(e)}
