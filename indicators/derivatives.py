"""
indicators/derivatives.py — MÓDULO 5: Dados de Derivativos
Open Interest, Funding Rate, Long/Short Ratio via Coinglass API.
Esses dados são exclusivos de cripto e muito poderosos para futuros.
"""
import requests
from config import COINGLASS_API_KEY


COINGLASS_BASE = "https://open-api.coinglass.com/public/v2"


def _headers():
    return {"coinglassSecret": COINGLASS_API_KEY}


def get_funding_rate(symbol: str = "BTC") -> dict:
    """Funding rate atual de BTC nos principais exchanges."""
    try:
        url = f"{COINGLASS_BASE}/funding"
        r   = requests.get(url, headers=_headers(), timeout=10)
        data = r.json().get("data", [])

        # Filtra pelo símbolo
        item = next((x for x in data if x.get("symbol", "").upper() == symbol.upper()), None)
        if not item:
            return {"error": "Símbolo não encontrado", "funding_rate": 0}

        # Pega a taxa da MEXC ou média geral
        exchanges   = item.get("uMarginList", [])
        mexc_entry  = next((x for x in exchanges if "mexc" in x.get("exchangeName", "").lower()), None)
        funding_val = float(mexc_entry["fundingRate"]) if mexc_entry else \
                      sum(float(x.get("fundingRate", 0)) for x in exchanges) / len(exchanges) if exchanges else 0

        return {
            "funding_rate":   round(funding_val * 100, 4),  # em %
            "funding_signal": _interpret_funding(funding_val),
        }
    except Exception as e:
        return {"error": str(e), "funding_rate": 0, "funding_signal": "INDISPONÍVEL"}


def get_open_interest(symbol: str = "BTC") -> dict:
    """Open Interest e variação recente."""
    try:
        url  = f"{COINGLASS_BASE}/open_interest"
        r    = requests.get(url, headers=_headers(), timeout=10)
        data = r.json().get("data", [])

        item = next((x for x in data if x.get("symbol", "").upper() == symbol.upper()), None)
        if not item:
            return {"error": "Símbolo não encontrado", "oi_change_pct": 0}

        oi_usd       = float(item.get("openInterest", 0))
        oi_change_1h = float(item.get("h1OIChangePercent", 0))
        oi_change_24h= float(item.get("h24OIChangePercent", 0))

        return {
            "oi_usd":         round(oi_usd / 1e9, 3),   # em bilhões
            "oi_change_1h":   round(oi_change_1h, 2),
            "oi_change_24h":  round(oi_change_24h, 2),
            "oi_signal":      _interpret_oi(oi_change_1h),
        }
    except Exception as e:
        return {"error": str(e), "oi_change_pct": 0, "oi_signal": "INDISPONÍVEL"}


def get_long_short_ratio(symbol: str = "BTC") -> dict:
    """Proporção de posições Long vs Short."""
    try:
        url  = f"{COINGLASS_BASE}/futures/longShortCurrentRate"
        params = {"symbol": symbol, "timeType": "1", "limit": 1}
        r    = requests.get(url, headers=_headers(), params=params, timeout=10)
        data = r.json().get("data", {})

        long_rate  = float(data.get("longRatio",  0.5)) * 100
        short_rate = float(data.get("shortRatio", 0.5)) * 100

        return {
            "long_pct":    round(long_rate, 2),
            "short_pct":   round(short_rate, 2),
            "ls_signal":   _interpret_ls_ratio(long_rate),
        }
    except Exception as e:
        return {"error": str(e), "long_pct": 50, "short_pct": 50, "ls_signal": "INDISPONÍVEL"}


def analyze(symbol: str = "BTC") -> dict:
    """
    Combina todos os dados de derivativos e retorna score.
    """
    funding = get_funding_rate(symbol)
    oi      = get_open_interest(symbol)
    ls      = get_long_short_ratio(symbol)

    score = 50  # neutro

    # Funding rate
    fr = funding.get("funding_rate", 0)
    if fr < -0.05:
        score += 20   # funding muito negativo → mercado short demais → possível alta
    elif fr < 0:
        score += 10
    elif fr > 0.1:
        score -= 20   # funding muito positivo → mercado long demais → possível queda
    elif fr > 0.05:
        score -= 10

    # OI
    oi_1h = oi.get("oi_change_1h", 0)
    if oi_1h > 2:
        score += 10   # OI crescendo = posições abrindo (tendência real)
    elif oi_1h < -2:
        score -= 5    # OI caindo = posições fechando

    # Long/Short ratio
    long_pct = ls.get("long_pct", 50)
    if long_pct > 75:
        score -= 20   # 75%+ long = mercado excessivamente comprado = perigo
    elif long_pct < 30:
        score += 20   # 30%- long = mercado muito short = possível squeeze de alta
    elif long_pct < 45:
        score += 10

    score = max(0, min(100, score))

    return {
        "score":      round(score),
        **funding,
        **oi,
        **ls,
        "summary": (
            f"Funding: {fr:.4f}% ({funding.get('funding_signal','?')}) | "
            f"OI: {oi.get('oi_usd','?')}B (1h: {oi_1h:+.1f}%) | "
            f"L/S: {ls.get('long_pct',50):.0f}% / {ls.get('short_pct',50):.0f}% "
            f"({ls.get('ls_signal','?')})"
        ),
    }


# ─── Funções de interpretação ──────────────────────────────────────────────

def _interpret_funding(rate: float) -> str:
    if rate > 0.001:   return "LONGS PAGANDO (sobrecomprado)"
    if rate > 0.0005:  return "LEVE PRESSÃO LONG"
    if rate < -0.001:  return "SHORTS PAGANDO (sobrevendido)"
    if rate < -0.0005: return "LEVE PRESSÃO SHORT"
    return "NEUTRO"


def _interpret_oi(change_1h: float) -> str:
    if change_1h > 3:   return "OI SUBINDO FORTE (tendência real)"
    if change_1h > 1:   return "OI SUBINDO (confirmação)"
    if change_1h < -3:  return "OI CAINDO FORTE (posições fechando)"
    if change_1h < -1:  return "OI CAINDO (cuidado)"
    return "OI ESTÁVEL"


def _interpret_ls_ratio(long_pct: float) -> str:
    if long_pct > 75:  return "EXCESSIVO LONG ⚠️ (queda provável)"
    if long_pct > 60:  return "MAIORIA LONG"
    if long_pct < 30:  return "EXCESSIVO SHORT 🚀 (squeeze provável)"
    if long_pct < 45:  return "MAIORIA SHORT"
    return "EQUILIBRADO"
