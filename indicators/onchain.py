"""
indicators/onchain.py — MÓDULO 6: Dados On-Chain
Exchange flows, whale transactions, MVRV via Glassnode.
"""
import requests
from config import GLASSNODE_API_KEY


GLASSNODE_BASE = "https://api.glassnode.com/v1/metrics"


def _get(endpoint: str, asset: str = "BTC", resolution: str = "24h") -> object:
    """Helper para buscar dados do Glassnode."""
    try:
        url    = f"{GLASSNODE_BASE}/{endpoint}"
        params = {"a": asset, "i": resolution, "api_key": GLASSNODE_API_KEY, "limit": 2}
        r      = requests.get(url, params=params, timeout=10)
        data   = r.json()
        if data and isinstance(data, list):
            return data[-1].get("v")
    except Exception:
        return None
    return None


def analyze(asset: str = "BTC") -> dict:
    """
    Busca e interpreta dados on-chain. Usa Glassnode API.
    Nota: o plano gratuito tem acesso limitado. Indicadores marcados com [FREE] funcionam sem pagar.
    """

    # [FREE] Exchange Net Flow (inflow - outflow)
    # Positivo = BTC entrando em exchanges (pressão de venda)
    # Negativo = BTC saindo de exchanges (acumulação)
    net_flow = _get("transactions/transfers_volume_exchanges_net", asset)

    # [FREE] Exchange Inflow
    inflow   = _get("transactions/transfers_volume_to_exchanges_sum", asset)

    # [FREE] Exchange Outflow
    outflow  = _get("transactions/transfers_volume_from_exchanges_sum", asset)

    # [PAGO] MVRV Z-Score — indica se o mercado está caro ou barato
    # > 7 = extremamente caro | < 0 = extremamente barato
    mvrv     = _get("market/mvrv_z_score", asset)

    # [FREE] Active Addresses
    active_addr = _get("addresses/active_count", asset)

    # --- Interpretação ---
    score = 50

    if net_flow is not None:
        if net_flow < 0:
            score += 15   # saindo das exchanges = acumulação = bullish
        elif net_flow > 0:
            score -= 15   # entrando = pressão de venda = bearish

    if mvrv is not None:
        if mvrv < 0:
            score += 20   # mercado barato historicamente
        elif mvrv > 5:
            score -= 20   # mercado caro historicamente
        elif mvrv > 3:
            score -= 10

    score = max(0, min(100, score))

    # Sinais textuais
    flow_signal = "INDISPONÍVEL"
    if net_flow is not None:
        if net_flow < -1000:
            flow_signal = "ACUMULAÇÃO FORTE 🟢"
        elif net_flow < 0:
            flow_signal = "ACUMULAÇÃO"
        elif net_flow > 1000:
            flow_signal = "DISTRIBUIÇÃO FORTE 🔴"
        else:
            flow_signal = "DISTRIBUIÇÃO"

    mvrv_signal = "INDISPONÍVEL"
    if mvrv is not None:
        if mvrv < 0:       mvrv_signal = "MUITO BARATO 🟢"
        elif mvrv < 2:     mvrv_signal = "NEUTRO"
        elif mvrv < 5:     mvrv_signal = "SOBREVALORIZADO ⚠️"
        else:              mvrv_signal = "EXTREMAMENTE CARO 🔴"

    return {
        "score":        round(score),
        "net_flow":     round(net_flow, 2) if net_flow else None,
        "inflow":       round(inflow, 2)   if inflow else None,
        "outflow":      round(outflow, 2)  if outflow else None,
        "mvrv":         round(mvrv, 3)     if mvrv else None,
        "active_addr":  active_addr,
        "flow_signal":  flow_signal,
        "mvrv_signal":  mvrv_signal,
        "summary": (
            f"Fluxo exchanges: {flow_signal} | "
            f"MVRV: {f'{mvrv:.2f}' if mvrv else 'N/A'} ({mvrv_signal}) | "
            f"Endereços ativos: {f'{int(active_addr):,}' if active_addr else 'N/A'}"
        ),
    }
