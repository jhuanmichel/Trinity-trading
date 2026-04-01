"""
indicators/correlation.py — CAMADA 5: Correlação Macro Cripto
Analisa: BTC (peso central), ETH, USDT Dominance, BTC Dominance
"""
import requests


def _coingecko_changes() -> dict:
    result = {
        "btc_change": 0.0,
        "eth_change": 0.0,
        "btc_dominance": 50.0,
        "usdt_dominance": 10.0,
    }
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=8,
        )
        data = r.json()
        result["btc_change"] = data.get("bitcoin", {}).get("usd_24h_change", 0.0) or 0.0
        result["eth_change"] = data.get("ethereum", {}).get("usd_24h_change", 0.0) or 0.0
    except Exception:
        pass

    try:
        r2 = requests.get("https://api.coingecko.com/api/v3/global", timeout=8)
        pct = r2.json().get("data", {}).get("market_cap_percentage", {})
        result["btc_dominance"]  = float(pct.get("btc", 50) or 50)
        result["usdt_dominance"] = float(pct.get("usdt", 10) or 10)
    except Exception:
        pass

    return result


def analyze() -> dict:
    cg          = _coingecko_changes()
    btc_change  = cg["btc_change"]
    eth_change  = cg["eth_change"]
    btc_dom     = cg["btc_dominance"]
    usdt_dom    = cg["usdt_dominance"]

    score   = 50
    signals = []

    # ── BTC — peso central (60% do score) ────────────────────────────────
    if btc_change > 3.0:
        score += 30
        signals.append(f"BTC {btc_change:+.1f}% forte alta 🟢")
    elif btc_change > 1.5:
        score += 18
        signals.append(f"BTC {btc_change:+.1f}% 🟢")
    elif btc_change > 0.5:
        score += 8
        signals.append(f"BTC {btc_change:+.1f}% leve")
    elif btc_change < -3.0:
        score -= 30
        signals.append(f"BTC {btc_change:+.1f}% forte queda 🔴")
    elif btc_change < -1.5:
        score -= 18
        signals.append(f"BTC {btc_change:+.1f}% 🔴")
    elif btc_change < -0.5:
        score -= 8
        signals.append(f"BTC {btc_change:+.1f}% leve")

    # ── ETH — confirma ou diverge (20%) ──────────────────────────────────
    if eth_change > 1.0 and btc_change > 0:
        score += 10
        signals.append(f"ETH {eth_change:+.1f}% confirma 🟢")
    elif eth_change < -1.0 and btc_change < 0:
        score -= 10
        signals.append(f"ETH {eth_change:+.1f}% confirma queda 🔴")
    elif eth_change > 1.0 and btc_change < 0:
        signals.append(f"ETH {eth_change:+.1f}% diverge de BTC ⚠️")
    elif eth_change < -1.0 and btc_change > 0:
        signals.append(f"ETH {eth_change:+.1f}% diverge de BTC ⚠️")

    # ── USDT Dominance — fuga para estável = bearish (10%) ───────────────
    if usdt_dom > 8.0:
        score -= 8
        signals.append(f"USDT.D {usdt_dom:.1f}% alto — fuga 🔴")
    elif usdt_dom < 5.0:
        score += 5
        signals.append(f"USDT.D {usdt_dom:.1f}% baixo — risco-on 🟢")

    # ── BTC Dominance — concentração em BTC (10%) ────────────────────────
    if btc_dom > 58:
        score += 5
        signals.append(f"BTC.D {btc_dom:.1f}% alta — capital em BTC 🟢")
    elif btc_dom < 45:
        score -= 3
        signals.append(f"BTC.D {btc_dom:.1f}% baixa — altseason")

    score = max(0, min(100, score))

    if score >= 60:
        bias = "LONG FAVORÁVEL"
    elif score <= 40:
        bias = "SHORT FAVORÁVEL"
    else:
        bias = "NEUTRO"

    return {
        "score":          round(score),
        "bias":           bias,
        "btc_change":     round(btc_change, 2),
        "eth_change":     round(eth_change, 2),
        "btc_dominance":  round(btc_dom, 1),
        "usdt_dominance": round(usdt_dom, 1),
        "signals":        signals,
        "summary": (
            f"Macro: {bias} | BTC {btc_change:+.1f}% | ETH {eth_change:+.1f}% | "
            f"BTC.D {btc_dom:.1f}% | USDT.D {usdt_dom:.1f}%"
        ),
    }
