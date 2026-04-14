"""
funding_extreme_scanner.py — Scanner Autônomo de Funding Rate Extremo

Varre TODOS os contratos MEXC Futures em 1 request (~200ms), detecta funding rates
extremos e emite alertas Telegram com formato específico de funding.

Pipeline:
  1. GET https://contract.mexc.com/api/v1/contract/ticker → todos os contratos
  2. Para cada ticker: calcular composite_score (level 0-40 + fuel 0-25 + ctx 0-25 + vol 0-10)
  3. Filtra composites >= WATCH_THRESHOLD
  4. Salva em dashboard/funding_extreme_latest.json
  5. Envia alertas Telegram para CRITICAL/HIGH (cooldown por símbolo)

Independente: falhas não afetam pump/crash radar.

Uso:
    from trinity.traders.funding_extreme_scanner import FundingExtremeScanner
    scanner = FundingExtremeScanner()
    result  = scanner.run_scan()
"""
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import requests

log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
BASE_DIR         = Path(__file__).parent.parent.parent
SCAN_OUTPUT_FILE = BASE_DIR / "dashboard" / "funding_extreme_latest.json"

MEXC_TICKER_URL  = "https://contract.mexc.com/api/v1/contract/ticker"

WATCH_THRESHOLD    = 40     # mínimo composite para aparecer no dashboard
ALERT_THRESHOLD    = 55     # mínimo para disparar alerta Telegram
CRITICAL_THRESHOLD = 75     # CRITICAL — alerta urgente

ALERT_COOLDOWN_S    = 3600  # 60min entre alertas normais do mesmo símbolo
CRITICAL_COOLDOWN_S = 1800  # 30min para alertas CRITICAL

MIN_VOLUME_USD      = 500_000   # filtro mínimo de liquidez

# Símbolos a excluir: stocks sintéticos, commodities, índices, ETFs
EXCLUDED_KEYWORDS = {
    'STOCK', 'ETF', 'US30', 'ALUMINUM', 'COPPER', 'GOLD', 'SILVER',
    'NVIDIA', 'NVDA', 'TESLA', 'TSLA', 'APPLE', 'AAPL', 'AMAZON', 'AMZN',
    'GOOGLE', 'GOOGL', 'GOOGLSTOCK', 'META', 'MSTR', 'COINBASE', 'MSFT',
    'MICROSOFT', 'NETFLIX', 'NFLX', 'AMD', 'INTEL', 'INTC', 'BABA',
    'OIL', 'CRUDE', 'NATGAS', 'WHEAT', 'CORN', 'PLATINUM', 'PALLADIUM',
    'EWY', 'EWJ', 'SPX', 'SPY', 'NDX', 'DJI', 'VIX', 'STABLE', 'RIVER',
    'SNDK', 'PYPL', 'UBER', 'ABNB', 'PLTR', 'SNAP', 'SHOP',
    'COIN', 'HOOD', 'SQ', 'ROKU', 'RBLX', 'PINS',
    'USOIL', 'UKOIL', 'XAUUSD', 'XAGUSD',
}

def _is_excluded(symbol: str) -> bool:
    """Retorna True se o símbolo é stock sintético, commodity ou índice."""
    upper = symbol.upper().replace('_USDT', '').replace('USDT', '')
    # Correspondência exata ou substring
    if upper in EXCLUDED_KEYWORDS:
        return True
    return any(kw in upper for kw in EXCLUDED_KEYWORDS)

TOP_LONGS  = 5   # top N shorts pagando longs
TOP_SHORTS = 5   # top N longs pagando shorts

_alert_cooldown: dict = {}   # {symbol: last_alert_ts}

# Tier map para composite_score
TIER_MAP = [
    (CRITICAL_THRESHOLD, "CRITICAL"),
    (ALERT_THRESHOLD,    "HIGH"),
    (WATCH_THRESHOLD,    "ELEVATED"),
    (0,                  "WATCH"),
]


# ── Dataclass ──────────────────────────────────────────────────────────────────

@dataclass
class FundingExtremeSignal:
    symbol:           str
    price:            float
    funding_rate:     float    # por 8h, decimal
    funding_ann:      float    # % ao ano
    daily_cost_pct:   float    # % custo/dia para o lado perdedor
    direction:        str      # LONG (fr<0, shorts pagando) / SHORT (fr>0, longs pagando)
    tier:             str      # CRITICAL / HIGH / ELEVATED / WATCH
    composite_score:  float    # 0-100
    level_score:      float    # 0-40
    fuel_score:       float    # 0-25
    context_score:    float    # 0-25
    volume_score:     float    # 0-10
    oi_usd:           float
    volume_24h:       float
    price_change_pct: float
    est_hours:        float    # estimativa de horas até squeeze
    signals:          List[str] = field(default_factory=list)
    scanned_at:       str = ""


# ── Core scanner ───────────────────────────────────────────────────────────────

class FundingExtremeScanner:
    """Scanner de funding rate extremo — varre todos os contratos MEXC em 1 request."""

    def run_scan(self) -> dict:
        """
        Executa o scan completo e retorna resultado para o dashboard.
        Salva em funding_extreme_latest.json.
        """
        t0 = time.time()
        scan_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            tickers = self._fetch_all_tickers()
        except Exception as e:
            log.error(f"[FundingExtreme] Erro ao buscar tickers: {e}")
            return self._empty_result(scan_ts)

        coins_scanned = len(tickers)
        signals: List[FundingExtremeSignal] = []

        for sym, ticker in tickers.items():
            sig = self._score_ticker(sym, ticker)
            if sig and sig.composite_score >= WATCH_THRESHOLD:
                signals.append(sig)

        # Separar LONG (funding negativo — shorts pagando) e SHORT (funding positivo)
        longs  = sorted(
            [s for s in signals if s.direction == "LONG"],
            key=lambda s: s.composite_score, reverse=True
        )
        shorts = sorted(
            [s for s in signals if s.direction == "SHORT"],
            key=lambda s: s.composite_score, reverse=True
        )

        result = {
            "scan_ts":         scan_ts,
            "scan_duration_s": round(time.time() - t0, 2),
            "coins_scanned":   coins_scanned,
            "extremes_found":  len(signals),
            "top_longs":       [asdict(s) for s in longs[:TOP_LONGS]],
            "top_shorts":      [asdict(s) for s in shorts[:TOP_SHORTS]],
            "all_extremes":    [asdict(s) for s in
                                sorted(signals, key=lambda s: s.composite_score, reverse=True)],
        }

        self._save(result)

        # Alertas Telegram (assíncrono não disponível aqui — chamada síncrona simples)
        self._send_alerts(longs + shorts)

        log.info(
            f"[FundingExtreme] OK: {coins_scanned} coins | "
            f"{len(signals)} extremos | L:{len(longs)} S:{len(shorts)} | "
            f"{result['scan_duration_s']:.1f}s"
        )
        return result

    # ── Privados ──────────────────────────────────────────────────────────────

    def _fetch_all_tickers(self) -> dict:
        """Busca todos os tickers MEXC Futures em 1 request. Retorna {symbol: ticker}."""
        r    = requests.get(MEXC_TICKER_URL, timeout=10)
        data = r.json().get("data", [])
        return {item["symbol"]: item for item in data if item.get("symbol")}

    def _score_ticker(self, symbol: str, ticker: dict) -> Optional[FundingExtremeSignal]:
        """Calcula composite score para um ticker. Retorna None se não relevante."""
        try:
            funding_rate = float(ticker.get("fundingRate", 0) or 0)
            price        = float(ticker.get("lastPrice", 0) or 0)
            hold_vol     = float(ticker.get("holdVol", 0) or 0)        # OI em contratos
            amount24     = float(ticker.get("amount24", 0) or 0)       # volume em USDT
            rise_fall    = float(ticker.get("riseFallRate", 0) or 0)   # decimal (0.05=+5%)
            pct_change   = rise_fall * 100.0

            if price <= 0:
                return None

            # Filtro: excluir stocks sintéticos, commodities, índices, ETFs
            if _is_excluded(symbol):
                return None

            # Filtro extra direto: padrões de índice/ETF no nome
            sym_upper = symbol.upper()
            if any(x in sym_upper for x in ['STOCK', 'SPX', 'NDX', 'DJI', 'EWJ', 'EWY', 'SPY']):
                return None

            # Filtro de volume mínimo
            oi_usd = hold_vol * price
            if amount24 < MIN_VOLUME_USD:
                return None

            # Sanity check de funding rate (exchange pode mandar em %)
            if abs(funding_rate) > 0.5:
                funding_rate /= 100.0
            if abs(funding_rate) > 0.10:
                return None  # dado anômalo

            # Funding irrelevante — ignorar
            if abs(funding_rate) < 0.0001:
                return None

            # Direção
            direction = "LONG" if funding_rate < 0 else "SHORT"

            funding_ann     = funding_rate * 3 * 365 * 100   # % ao ano
            daily_cost_pct  = abs(funding_rate) * 3 * 100    # % por dia

            signals = []

            # ── DIMENSÃO 1: NÍVEL (0-40) ──────────────────────────────────────
            abs_fr = abs(funding_rate)
            if   abs_fr >= 0.010:  level_score = 40; lbl = "MASSACRE"
            elif abs_fr >= 0.005:  level_score = 32; lbl = "EXTREMO"
            elif abs_fr >= 0.002:  level_score = 22; lbl = "ALTO"
            elif abs_fr >= 0.0005: level_score = 12; lbl = "ELEVADO"
            else:                  level_score = 0;  lbl = "NORMAL"

            if level_score > 0:
                dir_label = "shorts pagando longs" if direction == "LONG" else "longs pagando shorts"
                signals.append(
                    f"💰 Funding {funding_rate*100:+.4f}%/8h ({funding_ann:+,.0f}%/yr) "
                    f"— {lbl} — {dir_label}"
                )

            # ── DIMENSÃO 2: COMBUSTÍVEL OI (0-25) ────────────────────────────
            if   oi_usd >= 50_000_000: fuel_score = 25
            elif oi_usd >= 20_000_000: fuel_score = 20
            elif oi_usd >= 5_000_000:  fuel_score = 14
            elif oi_usd >= 1_000_000:  fuel_score = 8
            else:                      fuel_score = 3

            if oi_usd >= 20_000_000:
                signals.append(f"🔋 OI ${oi_usd/1e6:.1f}M — combustível para squeeze/liquidação")

            # ── DIMENSÃO 3: CONTEXTO DE PREÇO (0-25) ─────────────────────────
            if direction == "LONG":   # funding negativo: queremos short squeeze (preço subindo)
                if abs(pct_change) < 3.0:
                    context_score = 25
                    signals.append(f"💣 Preço FLAT ({pct_change:+.1f}%) com shorts presos — BOMBA ARMADA")
                elif pct_change > 3.0:
                    context_score = 20
                    signals.append(f"🚀 Preço +{pct_change:.1f}% — squeeze EM ANDAMENTO")
                elif pct_change > 0:   context_score = 14
                elif pct_change > -5:  context_score = 8
                else:                  context_score = 4
            else:  # funding positivo: queremos long liquidation (preço caindo)
                if abs(pct_change) < 3.0:
                    context_score = 25
                    signals.append(f"💣 Preço FLAT ({pct_change:+.1f}%) com longs presos — ARMADILHA")
                elif pct_change < -3.0:
                    context_score = 20
                    signals.append(f"📉 Preço {pct_change:.1f}% — liquidação EM ANDAMENTO")
                elif pct_change < 0:   context_score = 14
                elif pct_change < 5:   context_score = 8
                else:                  context_score = 4

            # ── DIMENSÃO 4: VOLUME (0-10) ─────────────────────────────────────
            if   amount24 >= 50_000_000: volume_score = 10
            elif amount24 >= 20_000_000: volume_score = 8
            elif amount24 >= 5_000_000:  volume_score = 5
            elif amount24 >= 1_000_000:  volume_score = 3
            else:                        volume_score = 1

            if amount24 >= 20_000_000:
                signals.append(f"📊 Volume ${amount24/1e6:.0f}M — atividade institucional intensa")

            # ── COMPOSITE: soma direta das dimensões (já ponderadas pelo teto)
            # level(0-40) + fuel(0-25) + context(0-25) + volume(0-10) = 0-100
            composite = level_score + fuel_score + context_score + volume_score

            # Tier
            tier = "WATCH"
            for thresh, name in TIER_MAP:
                if composite >= thresh:
                    tier = name
                    break

            # Estimativa de horas até squeeze (quanto tempo o custo de funding resolve)
            est_hours = round(8 / max(abs_fr / 0.001, 0.1), 1) if abs_fr > 0 else 999.0

            return FundingExtremeSignal(
                symbol=symbol,
                price=price,
                funding_rate=funding_rate,
                funding_ann=round(funding_ann, 2),
                daily_cost_pct=round(daily_cost_pct, 4),
                direction=direction,
                tier=tier,
                composite_score=round(composite, 2),
                level_score=level_score,
                fuel_score=fuel_score,
                context_score=context_score,
                volume_score=volume_score,
                oi_usd=round(oi_usd, 0),
                volume_24h=round(amount24, 0),
                price_change_pct=round(pct_change, 2),
                est_hours=est_hours,
                signals=signals,
                scanned_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

        except Exception as e:
            log.debug(f"[FundingExtreme] Erro ao processar {symbol}: {e}")
            return None

    def _save(self, result: dict):
        try:
            SCAN_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            SCAN_OUTPUT_FILE.write_text(json.dumps(result, indent=2, default=str))
        except Exception as e:
            log.warning(f"[FundingExtreme] Erro ao salvar: {e}")

    def _send_alerts(self, signals: List[FundingExtremeSignal]):
        """
        Funding extremo NÃO alerta sozinho via Telegram.
        Dados ficam no dashboard (/api/funding-extreme) e alimentam
        o Funding Extreme Engine nos scoring engines do pump/crash radar.
        Alertas Telegram só saem via pump/crash radar (score >= 75).
        """
        pass

    def _empty_result(self, scan_ts: str) -> dict:
        return {
            "scan_ts":         scan_ts,
            "scan_duration_s": 0,
            "coins_scanned":   0,
            "extremes_found":  0,
            "top_longs":       [],
            "top_shorts":      [],
            "all_extremes":    [],
        }


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_funding_telegram(s: FundingExtremeSignal):
    """Envia alerta Telegram específico de funding extremo."""
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
        import requests as _req

        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            return

        tier_emoji = {"CRITICAL": "🔥🔥🔥", "HIGH": "🔥🔥", "ELEVATED": "🔥", "WATCH": "⚡"}.get(s.tier, "⚡")
        dir_label  = "🚀 LONG SQUEEZE" if s.direction == "LONG" else "📉 LONG LIQUIDATION"
        header     = "🔴" if s.tier == "CRITICAL" else "🟠"

        sig_lines = ""
        for sig in s.signals[:3]:
            sig_lines += f"\n• {sig}"

        msg = (
            f"{header} *TRINITY — FUNDING EXTREME*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{tier_emoji} *{s.tier}* — {dir_label}\n"
            f"🪙 `{s.symbol}` | {s.price_change_pct:+.1f}% | ${s.price:,.4f}\n\n"
            f"💰 *Funding:* `{s.funding_rate*100:+.4f}%/8h` ({s.funding_ann:+,.0f}%/yr)\n"
            f"📅 *Custo diário:* `{s.daily_cost_pct:.3f}%/dia`\n"
            f"⏱ *Est. resolve:* `{s.est_hours:.0f}h`\n\n"
            f"🎯 *Composite Score:* `{s.composite_score:.0f}/100`\n"
            f"  Nível:    `{s.level_score:.0f}/40`\n"
            f"  Fuel OI:  `{s.fuel_score:.0f}/25`\n"
            f"  Contexto: `{s.context_score:.0f}/25`\n"
            f"  Volume:   `{s.volume_score:.0f}/10`\n\n"
            f"📊 OI: `${s.oi_usd/1e6:.1f}M` | Vol: `${s.volume_24h/1e6:.1f}M`\n\n"
            f"🔑 *SINAIS:*{sig_lines}"
        )

        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n_...truncado_"

        _req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=8,
        )
    except Exception as e:
        log.warning(f"[FundingExtreme] Telegram error: {e}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def run_funding_scan() -> dict:
    """Entry point para chamada direta pelo server.py."""
    scanner = FundingExtremeScanner()
    return scanner.run_scan()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_funding_scan()
    print(f"\nScan completo:")
    print(f"  Coins varridas: {result['coins_scanned']}")
    print(f"  Extremos encontrados: {result['extremes_found']}")
    print(f"  Top Longs: {len(result['top_longs'])}")
    print(f"  Top Shorts: {len(result['top_shorts'])}")
    print(f"  Duração: {result['scan_duration_s']}s")
