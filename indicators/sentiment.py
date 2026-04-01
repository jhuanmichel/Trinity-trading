"""
indicators/sentiment.py — MÓDULO 7: Sentimento do Mercado
Fear & Greed Index (gratuito), menções sociais via LunarCrush.
"""
import requests


def get_fear_greed() -> dict:
    """
    Fear & Greed Index da Alternative.me — completamente gratuito, sem API key.
    0 = Medo extremo | 100 = Ganância extrema
    """
    try:
        url  = "https://api.alternative.me/fng/?limit=2"
        r    = requests.get(url, timeout=10)
        data = r.json().get("data", [])

        if not data:
            return {"error": "Sem dados", "fg_value": 50}

        current  = data[0]
        previous = data[1] if len(data) > 1 else data[0]

        val      = int(current["value"])
        label    = current["value_classification"]
        prev_val = int(previous["value"])
        change   = val - prev_val

        return {
            "fg_value":     val,
            "fg_label":     label,
            "fg_change":    change,
            "fg_signal":    _interpret_fg(val),
        }
    except Exception as e:
        return {"error": str(e), "fg_value": 50, "fg_label": "Unknown", "fg_signal": "INDISPONÍVEL"}


def get_lunarcrush(symbol: str = "BTC") -> dict:
    """
    Dados de engajamento social via LunarCrush.
    Nota: requer API key do plano gratuito em lunarcrush.com
    """
    # Por padrão retorna placeholder — adicione sua chave para ativar
    return {
        "social_volume":   None,
        "social_score":    None,
        "galaxy_score":    None,
        "alt_rank":        None,
        "note": "Configure LUNARCRUSH_API_KEY para ativar dados sociais",
    }


def analyze() -> dict:
    """Combina sentimento e retorna score."""
    fg   = get_fear_greed()
    luna = get_lunarcrush()

    score = 50
    fg_val = fg.get("fg_value", 50)

    # Fear & Greed: extremos geram sinais contrários (contra-tendência)
    if fg_val <= 20:
        score += 25    # medo extremo = oportunidade de compra
    elif fg_val <= 30:
        score += 15
    elif fg_val >= 80:
        score -= 25    # ganância extrema = risco de topo
    elif fg_val >= 70:
        score -= 15

    score = max(0, min(100, score))

    return {
        "score":    round(score),
        **fg,
        **luna,
        "summary": (
            f"Fear & Greed: {fg_val}/100 — {fg.get('fg_label','?')} | "
            f"Sinal: {fg.get('fg_signal','?')} | "
            f"Variação: {fg.get('fg_change', 0):+d} desde ontem"
        ),
    }


def _interpret_fg(value: int) -> str:
    if value <= 20:  return "MEDO EXTREMO 🟢 (oportunidade contrária)"
    if value <= 35:  return "MEDO"
    if value <= 50:  return "NEUTRO BAIXISTA"
    if value <= 65:  return "NEUTRO ALTISTA"
    if value <= 80:  return "GANÂNCIA"
    return "GANÂNCIA EXTREMA 🔴 (risco de topo)"
