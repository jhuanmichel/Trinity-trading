"""
indicators/liquidations.py — Dados de Liquidações via Coinglass
"""
import requests
from config import COINGLASS_API_KEY


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
