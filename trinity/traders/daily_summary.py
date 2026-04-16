"""
daily_summary.py — Resumo diário enviado via Telegram às 08:00 UTC.

Uma mensagem compacta com tudo que aconteceu nas últimas 24h:
- BTC preço e variação
- Status do regime Trinity
- Sinais pump/crash ativos (top 3)
- Funding extremos
- Outcomes resolvidos (win rate)
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
_HEADERS  = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# Controle de envio: 1x por dia
_last_summary_date: str = ""


# ── Controle de envio ─────────────────────────────────────────────────────────

def should_send_summary() -> bool:
    """
    Retorna True uma vez por dia, quando o horário UTC estiver entre 08:00 e 08:09.
    Garante que o loop de 5 min sempre consegue acionar no primeiro check.
    """
    global _last_summary_date
    now   = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    if today == _last_summary_date:
        return False

    if now.hour == 8 and now.minute < 10:
        return True

    return False


# ── Coleta de dados ───────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    """Carrega JSON com fail-safe — retorna {} em caso de erro."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _collect_pump_crash() -> dict:
    """Top candidatos de pump e crash atuais."""
    result = {"pump_count": 0, "crash_count": 0, "top_pumps": [], "top_crashes": []}
    try:
        p = _load_json(BASE_DIR / "dashboard" / "pump_scan_latest.json")
        cands = p.get("candidates", [])
        result["pump_count"] = len(cands)
        result["top_pumps"] = [
            (c["symbol"], c.get("opportunity_score", 0))
            for c in cands[:3]
        ]
    except Exception:
        pass
    try:
        c = _load_json(BASE_DIR / "dashboard" / "crash_scan_latest.json")
        cands = c.get("candidates", [])
        result["crash_count"] = len(cands)
        result["top_crashes"] = [
            (c2["symbol"], c2.get("opportunity_score", 0))
            for c2 in cands[:3]
        ]
    except Exception:
        pass
    return result


def _collect_funding() -> dict:
    """Funding extremos ativos."""
    result = {"count": 0, "top": []}
    try:
        f = _load_json(BASE_DIR / "dashboard" / "funding_extreme_latest.json")
        result["count"] = f.get("extremes_found", 0)
        for e in f.get("top_longs", [])[:2]:
            result["top"].append({
                "symbol": e["symbol"],
                "rate":   f"{e['funding_rate'] * 100:+.3f}%/8h",
                "dir":    "LONG",
            })
        for e in f.get("top_shorts", [])[:2]:
            result["top"].append({
                "symbol": e["symbol"],
                "rate":   f"{e['funding_rate'] * 100:+.3f}%/8h",
                "dir":    "SHORT",
            })
    except Exception:
        pass
    return result


def _collect_outcomes() -> dict:
    """Win rate atual e contagem de pendentes."""
    result = {
        "wins": 0, "losses": 0, "total": 0,
        "win_rate": None, "pending": 0,
        "samples_needed": 30, "samples_collected": 0,
    }
    try:
        pf = BASE_DIR / "logs" / "pending_outcomes.jsonl"
        if pf.exists():
            lines = [l for l in pf.read_text().strip().split("\n") if l.strip()]
            result["pending"] = len(lines)
    except Exception:
        pass
    try:
        wr = _load_json(BASE_DIR / "dashboard" / "win_rate.json")
        result["wins"]   = wr.get("wins", 0)
        result["losses"] = wr.get("losses", 0)
        result["total"]  = result["wins"] + result["losses"]
        result["win_rate"] = wr.get("win_rate_pct")
        prog = wr.get("optimizer_progress", {})
        result["samples_collected"] = prog.get("samples_collected", 0)
        result["samples_needed"]    = prog.get("samples_needed", 30)
    except Exception:
        pass
    return result


def _collect_regime() -> dict:
    """Status do BTC Regime Trinity."""
    try:
        from trinity.traders.btc_regime_monitor import get_btc_regime
        return get_btc_regime() or {}
    except Exception:
        return {}


def _collect_btc_price() -> dict:
    """Preço e variação BTC via MEXC."""
    try:
        r = requests.get(
            "https://contract.mexc.com/api/v1/contract/ticker",
            headers=_HEADERS, timeout=10,
        )
        data = r.json()
        tickers = data.get("data", data) if isinstance(data, dict) else data
        btc = next((t for t in (tickers or []) if t.get("symbol") == "BTC_USDT"), None)
        if btc:
            return {
                "price":  float(btc.get("lastPrice", 0)),
                "change": float(btc.get("riseFallRate", 0)) * 100,
            }
    except Exception:
        pass
    return {"price": 0, "change": 0}


# ── Formatar mensagem ─────────────────────────────────────────────────────────

def _format_message(now: datetime, btc: dict, regime: dict,
                    signals: dict, funding: dict, outcomes: dict) -> str:
    date_str = now.strftime("%d/%m/%Y")

    # Header
    lines = [
        "📋 *TRINITY — RESUMO DIÁRIO*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 {date_str} | 08:00 UTC",
        "",
    ]

    # BTC price
    btc_emoji = "🟢" if btc["change"] > 0 else "🔴" if btc["change"] < 0 else "⚪"
    price_str = f"${btc['price']:,.0f}" if btc["price"] else "indisponível"
    lines.append(f"{btc_emoji} *BTC: {price_str}* ({btc['change']:+.1f}%)" if btc["price"] else f"⚪ *BTC: indisponível*")
    lines.append("")

    # Regime
    if regime:
        direction  = regime.get("direction", "?")
        strength   = regime.get("strength", 0)
        bb_bias    = regime.get("bull_band_bias", "?")
        bb_dist    = regime.get("bull_band_dist", 0)
        nfci_val   = regime.get("nfci_value", 0)
        nfci_reg   = regime.get("nfci_regime", "?")
        m2_yoy     = regime.get("m2_yoy_pct", 0)
        m2_dir     = regime.get("m2_direction", "?")
        lines.append(f"🏛️ *REGIME:* {direction} (força={strength:.0f})")
        lines.append(f"  Bull Band: {bb_bias} ({bb_dist:+.1f}%)")
        lines.append(f"  NFCI: {nfci_val:.3f} ({nfci_reg})")
        lines.append(f"  M2 YoY: {m2_yoy:+.1f}% ({m2_dir})")
    else:
        lines.append("🏛️ *REGIME:* Indisponível")
    lines.append("")

    # Sinais pump/crash
    lines.append("📡 *SINAIS ATIVOS:*")
    pump_line = f"  🚀 Pump: {signals['pump_count']} candidatos"
    if signals["top_pumps"]:
        pump_line += " | top: " + ", ".join(f"{s}={sc:.0f}" for s, sc in signals["top_pumps"])
    lines.append(pump_line)

    crash_line = f"  📉 Crash: {signals['crash_count']} candidatos"
    if signals["top_crashes"]:
        crash_line += " | top: " + ", ".join(f"{s}={sc:.0f}" for s, sc in signals["top_crashes"])
    lines.append(crash_line)

    # Funding
    lines.append(f"  💸 Funding extremos: {funding['count']}")
    for fe in funding["top"][:3]:
        lines.append(f"    {fe['symbol']} {fe['rate']} ({fe['dir']})")
    lines.append("")

    # Outcomes / win rate
    lines.append("📊 *PERFORMANCE:*")
    if outcomes["total"] > 0:
        wr = outcomes["win_rate"]
        if wr is not None:
            wr_emoji = "🟢" if wr >= 50 else "🟡" if wr >= 35 else "🔴"
            lines.append(f"  {wr_emoji} Win rate: {wr:.1f}% ({outcomes['wins']}W / {outcomes['losses']}L)")
        else:
            lines.append(f"  ⚪ Win rate: dados insuficientes ({outcomes['wins']}W / {outcomes['losses']}L)")
    else:
        lines.append(f"  ⏳ Coletando dados... ({outcomes['pending']} pendentes)")

    sc = outcomes["samples_collected"]
    sn = outcomes["samples_needed"]
    lines.append(f"  Amostras: {sc}/{sn} para otimização")
    lines.append("")

    # Rodapé
    lines.append("⚡ _Sistema operacional — próximo resumo em 24h_")

    msg = "\n".join(lines)

    # Truncamento de segurança
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n_...truncado_"

    return msg


# ── Envio ─────────────────────────────────────────────────────────────────────

def send_daily_summary():
    """Coleta todos os dados e envia resumo diário no Telegram."""
    global _last_summary_date
    now = datetime.now(timezone.utc)

    try:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            log.warning("[DailySummary] TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID não configurados")
            return

        # Coletar dados em paralelo (fail-safe individual)
        btc     = _collect_btc_price()
        regime  = _collect_regime()
        signals = _collect_pump_crash()
        funding = _collect_funding()
        outcomes = _collect_outcomes()

        msg = _format_message(now, btc, regime, signals, funding, outcomes)

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       msg,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )

        _last_summary_date = now.strftime("%Y-%m-%d")
        log.info(f"[DailySummary] Resumo enviado — {now.strftime('%d/%m/%Y %H:%M')} UTC")

    except Exception as e:
        log.error(f"[DailySummary] Erro ao enviar resumo: {e}")


def force_send_summary():
    """Força envio imediato do resumo (ignora o controle de data). Para teste."""
    global _last_summary_date
    _last_summary_date = ""
    send_daily_summary()
