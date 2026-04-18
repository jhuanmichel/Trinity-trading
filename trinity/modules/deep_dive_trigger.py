"""
trinity/modules/deep_dive_trigger.py — Análise institucional automática via Claude API.

Disparado pelos detectores quando movimento extremo é detectado (pump >200% ou crash >-30%,
ou score >= 80). Envia análise completa no Telegram logo após o alerta numérico do scanner.

Framework de 5 pilares (Vol I — Trinity Pump Detector Module):
  Pilar 1: On-Chain    (adaptado — sem Arkham; usa funding/OI como proxy)
  Pilar 2: Tokenomics  (market cap, volume)
  Pilar 3: Derivativos (funding, OI, liquidações — via MEXC API)
  Pilar 4: Técnico     (RSI, Bollinger, ATR, volume — calculado dos klines)
  Pilar 5: Fundamento  (narrativa — via inferência Haiku)

Rate limits internos:
  - 4h cooldown por símbolo
  - 5 análises máx por dia
  - Auto-shutdown 24h em erro de crédito (402/429)
  - Timeout 30s por chamada

Custo estimado: ~$0.003-0.005 por análise (Haiku)
5/dia = ~$0.75/mês
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import time
from datetime import datetime, timedelta
from typing import Any

import requests

log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
LOGS_DIR = (pathlib.Path("/data/logs") if pathlib.Path("/data").exists()
            else pathlib.Path("logs"))
LOG_FILE = "deep_dives.jsonl"

# ── Configuração ───────────────────────────────────────────────────────────────
MODEL            = "claude-haiku-4-5-20251001"
MAX_TOKENS       = 2000
TEMPERATURE      = 0.3
TIMEOUT_SECONDS  = 30
RETRY_COUNT      = 1
RETRY_BACKOFF    = 5           # segundos entre retries

COOLDOWN_HOURS        = 4     # por símbolo
MAX_DAILY_ANALYSES    = 5     # total por dia
CREDIT_LOCKOUT_HOURS  = 24   # auto-shutdown em erro de crédito

# Thresholds de trigger
MIN_SCORE_TRIGGER  = 80       # score mínimo do detector
MIN_OVEREXT_PUMP   = 200      # % de pump para trigger direto
MIN_OVEREXT_CRASH  = -30      # % de queda para trigger direto


# ══════════════════════════════════════════════════════════════════════════════
# Indicadores técnicos (stdlib puro)
# ══════════════════════════════════════════════════════════════════════════════

def _rsi(closes: list, period: int = 14) -> float:
    """RSI Wilder padrão."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 1)


def _atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Average True Range."""
    if len(highs) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i]  - closes[i - 1]))
        trs.append(tr)
    return round(sum(trs[-period:]) / period, 8)


def _bollinger(closes: list, period: int = 20) -> dict:
    """Bollinger Bands + breach_ratio (Vol I §5.2)."""
    if len(closes) < period:
        return {"upper": 0, "mid": 0, "lower": 0, "breach_ratio": 0}
    window   = closes[-period:]
    mid      = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std      = variance ** 0.5
    upper    = mid + 2 * std
    lower    = mid - 2 * std
    price    = closes[-1]
    width    = upper - mid
    breach   = (price - upper) / width if width > 0 else 0
    return {
        "upper":        round(upper, 8),
        "mid":          round(mid, 8),
        "lower":        round(lower, 8),
        "breach_ratio": round(breach, 2),
    }


def _consecutive(closes: list, direction: str) -> int:
    """Conta candles consecutivas na mesma direção."""
    count = 0
    for i in range(len(closes) - 1, 0, -1):
        up = direction == "up" and closes[i] > closes[i - 1]
        dn = direction == "down" and closes[i] < closes[i - 1]
        if up or dn:
            count += 1
        else:
            break
    return count


# ══════════════════════════════════════════════════════════════════════════════
# Classe principal
# ══════════════════════════════════════════════════════════════════════════════

class DeepDiveTrigger:
    """
    Análise institucional automática via Claude Haiku.

    Instanciar apenas uma vez via trinity.modules.get_deep_dive_trigger().
    """

    def __init__(self) -> None:
        self._cooldowns:       dict[str, datetime] = {}
        self._daily_count:     int = 0
        self._daily_reset:     Any = None          # date do último reset
        self._disabled_until:  datetime | None = None
        self._api_key:         str = self._resolve_key()

        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log.info(
            "[DeepDive] Inicializado | model=%s | cooldown=%dh | max_daily=%d",
            MODEL, COOLDOWN_HOURS, MAX_DAILY_ANALYSES,
        )

    # ── Chave API ─────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_key() -> str:
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if key:
            return key
        try:
            from config import ANTHROPIC_API_KEY as _k
            return _k or ""
        except Exception:
            return ""

    # ── Rate limits ───────────────────────────────────────────────────────────

    def _can_analyze(self, symbol: str) -> tuple[bool, str]:
        """Verifica todos os gates antes de chamar a API."""
        now = datetime.utcnow()

        if self._disabled_until and now < self._disabled_until:
            h = (self._disabled_until - now).total_seconds() / 3600
            return False, f"API desativada por erro de crédito (reativa em {h:.1f}h)"

        if not self._api_key:
            return False, "ANTHROPIC_API_KEY não configurada"

        today = now.date()
        if self._daily_reset != today:
            self._daily_count = 0
            self._daily_reset = today
            log.info("[DeepDive] Contador diário resetado")

        if self._daily_count >= MAX_DAILY_ANALYSES:
            return False, f"Limite diário atingido ({MAX_DAILY_ANALYSES}/{MAX_DAILY_ANALYSES})"

        if symbol in self._cooldowns:
            elapsed_h = (now - self._cooldowns[symbol]).total_seconds() / 3600
            if elapsed_h < COOLDOWN_HOURS:
                remaining = COOLDOWN_HOURS - elapsed_h
                return False, f"Cooldown ativo para {symbol} (resta {remaining:.1f}h)"

        return True, "OK"

    # ── Trigger público ───────────────────────────────────────────────────────

    def should_trigger(self, score: float, overextension_pct: float,
                       direction: str) -> bool:
        """
        True se o movimento justifica um deep dive.
        Critérios (qualquer um é suficiente — Vol I §7.2):
          - Score >= 80 (CRÍTICO)
          - Pump > +200%
          - Crash < -30%
        """
        if score >= MIN_SCORE_TRIGGER:
            return True
        if direction == "PUMP" and overextension_pct >= MIN_OVEREXT_PUMP:
            return True
        if direction == "CRASH" and overextension_pct <= MIN_OVEREXT_CRASH:
            return True
        return False

    # ── Ponto de entrada ──────────────────────────────────────────────────────

    async def analyze(self, symbol: str, direction: str,
                      context: dict) -> dict | None:
        """
        Ponto de entrada principal. Chamado via asyncio.create_task().

        context esperado:
          score, component_scores, overextension_pct (price_change_pct),
          price_current (price), price_change_pct, volume_24h,
          funding_rate, oi_change_pct, klines_4h, klines_1h,
          is_blue_chip, manipulation_detected
        """
        can_run, reason = self._can_analyze(symbol)
        if not can_run:
            log.info("[DeepDive] BLOQUEADO %s: %s", symbol, reason)
            return None

        log.info("[DeepDive] INICIANDO %s (%s) | score=%.0f | overext=%.1f%%",
                 symbol, direction,
                 context.get("score", 0),
                 context.get("overextension_pct", context.get("price_change_pct", 0)))
        try:
            enriched       = self._enrich(symbol, direction, context)
            system_p, user_p = self._build_prompt(symbol, direction, enriched)

            # Chamada síncrona num thread separado — não bloqueia o event loop
            analysis_text  = await asyncio.to_thread(
                self._call_claude_sync, system_p, user_p
            )

            if not analysis_text:
                log.warning("[DeepDive] API retornou vazio para %s", symbol)
                return None

            # Telegram (também thread para não bloquear)
            await asyncio.to_thread(
                self._send_telegram_sync, symbol, direction, enriched, analysis_text
            )

            entry = self._save_log(symbol, direction, enriched, analysis_text)

            self._cooldowns[symbol] = datetime.utcnow()
            self._daily_count += 1

            log.info("[DeepDive] COMPLETO %s | diário: %d/%d",
                     symbol, self._daily_count, MAX_DAILY_ANALYSES)
            return entry

        except Exception as exc:
            log.error("[DeepDive] ERRO %s: %s", symbol, exc, exc_info=True)
            return None

    # ── Context enricher ──────────────────────────────────────────────────────

    def _enrich(self, symbol: str, direction: str, ctx: dict) -> dict:
        """Enriquece contexto com indicadores derivados dos klines disponíveis."""
        enriched = dict(ctx)

        # Klines 4h
        klines_4h = ctx.get("klines_4h", [])
        if klines_4h and len(klines_4h) >= 14:
            def _f(k, i):
                return float(k[i]) if isinstance(k, (list, tuple)) else float(k.get(
                    ["open", "high", "low", "close", "volume"][i - 1] if i > 0 else "open", 0
                ))
            closes_4h  = [_f(k, 4) for k in klines_4h]
            highs_4h   = [_f(k, 2) for k in klines_4h]
            lows_4h    = [_f(k, 3) for k in klines_4h]
            vols_4h    = [_f(k, 5) for k in klines_4h]

            enriched["rsi_4h"]  = _rsi(closes_4h)
            enriched["atr_4h"]  = _atr(highs_4h, lows_4h, closes_4h)
            bb = _bollinger(closes_4h)
            enriched.update({f"bb_{k}": v for k, v in bb.items()})
            enriched["consecutive_green"] = _consecutive(closes_4h, "up")
            enriched["consecutive_red"]   = _consecutive(closes_4h, "down")

            if len(vols_4h) >= 42:
                avg_vol_7d = sum(vols_4h[-42:]) / 42
                enriched["volume_ratio_7d"] = round(
                    vols_4h[-1] / avg_vol_7d if avg_vol_7d > 0 else 0, 1
                )

        # Klines 1h
        klines_1h = ctx.get("klines_1h", [])
        if klines_1h and len(klines_1h) >= 14:
            closes_1h = [
                float(k[4]) if isinstance(k, (list, tuple)) else float(k.get("close", 0))
                for k in klines_1h
            ]
            enriched["rsi_1h"] = _rsi(closes_1h)

        # Classificação do score (Vol I §7.2)
        score = ctx.get("score", 0)
        if score >= 90:
            enriched["classification"] = "PARABÓLICA TERMINAL"
        elif score >= 80:
            enriched["classification"] = "CRÍTICO"
        elif score >= 60:
            enriched["classification"] = "ALTO RISCO"
        elif score >= 41:
            enriched["classification"] = "ATENÇÃO"
        else:
            enriched["classification"] = "NORMAL"

        # Funding anualizado (Vol I §4.2)
        funding_8h = ctx.get("funding_rate", 0)
        fa = round(funding_8h * 3 * 365, 1)
        enriched["funding_annualized"] = fa
        if fa > 200:
            enriched["funding_class"] = "EUFORIA EXTREMA — topo iminente"
        elif fa > 100:
            enriched["funding_class"] = "Sobrecomprado"
        elif fa > 30:
            enriched["funding_class"] = "Normal em alta"
        elif fa > -30:
            enriched["funding_class"] = "Equilíbrio"
        elif fa > -100:
            enriched["funding_class"] = "Shorts pagando — risco squeeze"
        else:
            enriched["funding_class"] = "SQUEEZE IMINENTE"

        # BTC regime (busca se não vier no contexto)
        if "btc_regime" not in enriched:
            try:
                from trinity.traders.btc_regime_monitor import get_btc_regime as _gbr
                _reg = _gbr()
                enriched["btc_regime"]       = _reg.get("direction", "UNKNOWN")
                enriched["btc_regime_score"] = _reg.get("strength", 0)
            except Exception:
                enriched["btc_regime"]       = "UNKNOWN"
                enriched["btc_regime_score"] = 0

        return enriched

    # ── Prompt Engine (5 pilares) ──────────────────────────────────────────────

    def _build_prompt(self, symbol: str, direction: str,
                      ctx: dict) -> tuple[str, str]:
        """Monta system + user prompt. Retorna (system_prompt, user_prompt)."""

        system_prompt = (
            "Você é um analista quant sênior de um fundo crypto sistemático.\n"
            "Produza análises rápidas e acionáveis sobre movimentos extremos de tokens.\n\n"
            "REGRAS:\n"
            "1. FACTUAL — cada afirmação ancorada nos dados fornecidos.\n"
            "2. PROBABILÍSTICO — use probabilidades, não certezas.\n"
            "3. Para cada tese, identifique o maior risco de estar errado.\n"
            "4. Termine com setup acionável (entry zone, stop, TPs).\n"
            "5. Responda em PORTUGUÊS BRASILEIRO.\n"
            "6. Máximo 800 palavras. Narrativa fluida — não repita dados brutos.\n\n"
            "GESTÃO DE RISCO (inegociável — Vol I §8):\n"
            "  Risco por trade: 1% do equity\n"
            "  Alavancagem máx: 3×\n"
            "  Stop: max(ATH+5%, high 3 candles +3%, entry + 2×ATR14)\n"
            "  TP escalonado: TP1 30% em retrace 30%, TP2 30% em 50%,\n"
            "                 TP3 25% em 70%, Runner 15% trailing 2×ATR\n"
            "  Timeout: 7 dias sem movimento >10% → break-even\n"
            "  Kill: novo ATH → fechar tudo (tese quebrada)"
        )

        # Dados técnicos (N/A se não disponíveis)
        rsi_4h    = ctx.get("rsi_4h", "N/A")
        rsi_1h    = ctx.get("rsi_1h", "N/A")
        bb_breach = ctx.get("bb_breach_ratio", ctx.get("bb_breach", "N/A"))
        atr_4h    = ctx.get("atr_4h", "N/A")
        vol_ratio = ctx.get("volume_ratio_7d", "N/A")
        consec_g  = ctx.get("consecutive_green", "N/A")
        consec_r  = ctx.get("consecutive_red", "N/A")

        # RSI assessment qualitativo (Vol I §5.1)
        rsi_assessment = "N/A"
        if isinstance(rsi_4h, (int, float)):
            if rsi_4h > 90:
                rsi_assessment = "EXTREMO — precede correção em >70% dos casos"
            elif rsi_4h > 80:
                rsi_assessment = "Sobrecomprado forte"
            elif rsi_4h > 70:
                rsi_assessment = "Sobrecomprado"
            elif rsi_4h > 30:
                rsi_assessment = "Neutro"
            elif rsi_4h > 20:
                rsi_assessment = "Sobrevendido"
            else:
                rsi_assessment = "EXTREMO sobrevendido — possível bounce"

        # Breakdown sub-detectores
        det_scores = ctx.get("component_scores", {})
        det_lines  = "\n".join(f"  - {k}: {v:.1f}" for k, v in det_scores.items()) or "  Não disponível"

        # Resumo klines 4h (últimas 6 candles)
        klines_summary = ""
        for k in ctx.get("klines_4h", [])[-6:]:
            try:
                if isinstance(k, (list, tuple)):
                    o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
                else:
                    o, h, l, c, v = (float(k.get(f, 0)) for f in ("open", "high", "low", "close", "volume"))
                chg = (c - o) / o * 100 if o > 0 else 0
                em  = "🟢" if c >= o else "🔴"
                klines_summary += f"  {em} O:{o:.4g} H:{h:.4g} L:{l:.4g} C:{c:.4g} ({chg:+.1f}%)\n"
            except Exception:
                continue

        overext = ctx.get("overextension_pct", ctx.get("price_change_pct", "N/A"))
        fa      = ctx.get("funding_annualized", 0)

        user_prompt = (
            f"🔬 DEEP DIVE — {symbol} ({direction})\n\n"
            f"═══ DADOS DO MOVIMENTO ═══\n"
            f"Preço atual:      ${ctx.get('price_current', ctx.get('price', 'N/A'))}\n"
            f"Variação 24h:     {ctx.get('price_change_pct', 'N/A')}%\n"
            f"Overextension:    {overext}%\n"
            f"Market Cap:       ${ctx.get('market_cap', 'N/A')}\n"
            f"Volume 24h:       ${ctx.get('volume_24h', 'N/A')}\n"
            f"Blue Chip:        {'Sim' if ctx.get('is_blue_chip') else 'Não'}\n"
            f"Manipulação:      {'SIM ⚠️' if ctx.get('manipulation_detected') else 'Não'}\n\n"

            f"═══ SCORE TRINITY ═══\n"
            f"Score composto:   {ctx.get('score', 'N/A')}/100\n"
            f"Classificação:    {ctx.get('classification', 'N/A')}\n"
            f"Sub-detectores:\n{det_lines}\n\n"

            f"═══ PILAR 3: DERIVATIVOS ═══\n"
            f"Funding rate (8h):    {ctx.get('funding_rate', 'N/A')}%\n"
            f"Funding anualizado:   {fa:.0f}%\n"
            f"Classificação:        {ctx.get('funding_class', 'N/A')}\n"
            f"Variação OI:          {ctx.get('oi_change_pct', 'N/A')}%\n"
            f"Nota: Funding >200% ann = euforia extrema, topo iminente (>70% dos casos).\n"
            f"      OI caindo com preço subindo = DISTRIBUIÇÃO (bearish forte).\n"
            f"      Se queda > 1.5× custo diário do funding → funding alto NÃO garante squeeze.\n\n"

            f"═══ PILAR 4: TÉCNICO ═══\n"
            f"RSI 4h:               {rsi_4h} — {rsi_assessment}\n"
            f"RSI 1h:               {rsi_1h}\n"
            f"Bollinger breach:     {bb_breach} (>1.0 = extremo)\n"
            f"ATR 14 (4h):          {atr_4h}\n"
            f"Volume ratio vs 7d:   {vol_ratio}×\n"
            f"Candles verdes consec: {consec_g}\n"
            f"Candles vermelhas:     {consec_r}\n"
        )

        if klines_summary:
            user_prompt += f"\nÚltimas 6 candles 4h:\n{klines_summary}"

        user_prompt += (
            f"\n═══ CONTEXTO MACRO ═══\n"
            f"Regime BTC:      {ctx.get('btc_regime', 'N/A')}\n"
            f"Força regime:    {ctx.get('btc_regime_score', 'N/A')}/100\n\n"

            f"═══ ANÁLISE SOLICITADA (5 pilares) ═══\n\n"
            f"1. ON-CHAIN / TOKENOMICS (proxy via volume/market cap):\n"
            f"   - Este padrão é consistente com pump coordenado (low float, concentração)?\n"
            f"   - Volume relativo ao market cap: distribuição ou acumulação?\n"
            f"   - Movimento orgânico ou alavancado?\n\n"
            f"2. DERIVATIVOS:\n"
            f"   - Funding + OI indicam exaustão ou continuação?\n"
            f"   - Há combustível para squeeze ou já é distribuição?\n\n"
            f"3. TÉCNICO:\n"
            f"   - RSI e Bollinger confirmam extremo?\n"
            f"   - Volume sustenta ou está morrendo?\n"
            f"   - Divergências presentes?\n\n"
            f"4. CONTEXTO BTC:\n"
            f"   - Regime macro favorece ou prejudica este trade?\n\n"
            f"5. FUNDAMENTO (narrativa):\n"
            f"   - Token com tese ou puro especulativo?\n"
            f"   - Movimento proporcional ao fundamento?\n\n"
            f"CONCLUSÃO OBRIGATÓRIA:\n"
            f"a) Probabilidade de continuação nas próximas 24h: X%\n"
            f"b) Probabilidade de reversão 30%+ nas próximas 48h: X%\n"
            f"c) Setup sugerido (entry zone, stop, TP1/TP2/TP3)\n"
            f"d) Risco por trade recomendado (% do capital)\n"
            f"e) Top 3 riscos de operar\n"
            f"f) Veredicto: OPERAR / AGUARDAR TRIGGER / NÃO OPERAR"
        )

        return system_prompt, user_prompt

    # ── Chamada Claude (síncrona, rodada em thread) ───────────────────────────

    def _call_claude_sync(self, system_prompt: str, user_prompt: str) -> str | None:
        """
        Chama Claude Haiku via requests.post (padrão do analysis_engine.py).
        Síncrona — chamar via asyncio.to_thread().
        """
        for attempt in range(1 + RETRY_COUNT):
            try:
                resp = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "Content-Type":    "application/json",
                        "x-api-key":       self._api_key,
                        "anthropic-version": "2023-06-01",
                    },
                    json={
                        "model":       MODEL,
                        "max_tokens":  MAX_TOKENS,
                        "temperature": TEMPERATURE,
                        "system":      system_prompt,
                        "messages":    [{"role": "user", "content": user_prompt}],
                    },
                    timeout=TIMEOUT_SECONDS,
                )

                if resp.status_code == 401:
                    log.error("[DeepDive] API key inválida (401)")
                    self._disable_api(CREDIT_LOCKOUT_HOURS)
                    return None

                if resp.status_code in (402, 529):
                    log.error("[DeepDive] Créditos insuficientes (%d)", resp.status_code)
                    self._disable_api(CREDIT_LOCKOUT_HOURS)
                    return None

                if resp.status_code == 429:
                    if attempt < RETRY_COUNT:
                        log.warning("[DeepDive] Rate limit — retry em %ds", RETRY_BACKOFF)
                        time.sleep(RETRY_BACKOFF)
                        continue
                    log.error("[DeepDive] Rate limit persistente")
                    return None

                if resp.status_code != 200:
                    log.error("[DeepDive] HTTP %d: %s", resp.status_code, resp.text[:200])
                    if attempt < RETRY_COUNT:
                        time.sleep(RETRY_BACKOFF)
                        continue
                    return None

                data   = resp.json()
                text   = "".join(
                    b.get("text", "") for b in data.get("content", [])
                    if b.get("type") == "text"
                )
                usage  = data.get("usage", {})
                inp_tk = usage.get("input_tokens", 0)
                out_tk = usage.get("output_tokens", 0)
                cost   = (inp_tk * 0.25 + out_tk * 1.25) / 1_000_000
                log.info("[DeepDive] API OK | %din/%dout | ~$%.4f", inp_tk, out_tk, cost)
                return text

            except requests.exceptions.Timeout:
                log.warning("[DeepDive] Timeout %ds (tentativa %d)", TIMEOUT_SECONDS, attempt + 1)
                if attempt < RETRY_COUNT:
                    time.sleep(RETRY_BACKOFF)
                else:
                    return None

            except Exception as exc:
                err = str(exc).lower()
                if any(kw in err for kw in ("credit", "balance", "402")):
                    log.error("[DeepDive] Créditos insuficientes: %s", exc)
                    self._disable_api(CREDIT_LOCKOUT_HOURS)
                    return None
                log.warning("[DeepDive] Erro API (tentativa %d): %s", attempt + 1, exc)
                if attempt < RETRY_COUNT:
                    time.sleep(RETRY_BACKOFF)
                else:
                    return None

        return None

    def _disable_api(self, hours: int) -> None:
        """Desativa a API por N horas (controle de custo)."""
        self._disabled_until = datetime.utcnow() + timedelta(hours=hours)
        log.warning("[DeepDive] AUTO-DESATIVADO por %dh (erro de crédito)", hours)

    # ── Envio Telegram (síncrono, rodado em thread) ───────────────────────────

    def _send_telegram_sync(self, symbol: str, direction: str,
                             ctx: dict, analysis: str) -> None:
        """Envia análise no Telegram como mensagem separada após o alerta normal."""
        try:
            from alerts import send_message

            emoji_dir      = "📈" if direction == "PUMP" else "📉"
            score          = ctx.get("score", "N/A")
            classification = ctx.get("classification", "")
            overext        = ctx.get("overextension_pct", ctx.get("price_change_pct", "N/A"))

            # Trunca para limite Telegram (4096 chars)
            max_len = 3500
            if len(analysis) > max_len:
                analysis = analysis[:max_len] + "\n\n[...análise truncada]"

            msg = (
                f"🔬 <b>DEEP DIVE — {symbol}</b>\n"
                f"{emoji_dir} {direction} | Score: {score} | {classification}\n"
                f"Movimento: {overext}%\n"
                f"{'━' * 30}\n\n"
                f"{analysis}\n\n"
                f"{'━' * 30}\n"
                f"⚠️ <i>Análise automática (Claude Haiku) — não é recomendação.</i>"
            )

            ok = send_message(msg)
            if not ok:
                # Fallback sem tags HTML
                msg_plain = (msg.replace("<b>", "").replace("</b>", "")
                                .replace("<i>", "").replace("</i>", ""))
                send_message(msg_plain)
        except Exception as exc:
            log.error("[DeepDive] Falha ao enviar Telegram: %s", exc)

    # ── Persistência ──────────────────────────────────────────────────────────

    def _save_log(self, symbol: str, direction: str,
                  ctx: dict, analysis: str) -> dict:
        """Persiste deep dive em JSONL para histórico e post-mortem (Vol II §11)."""
        entry = {
            "timestamp":            datetime.utcnow().isoformat(),
            "symbol":               symbol,
            "direction":            direction,
            "score":                ctx.get("score"),
            "classification":       ctx.get("classification"),
            "overextension_pct":    ctx.get("overextension_pct", ctx.get("price_change_pct")),
            "price_at_analysis":    ctx.get("price_current", ctx.get("price")),
            "funding_rate":         ctx.get("funding_rate"),
            "funding_annualized":   ctx.get("funding_annualized"),
            "oi_change_pct":        ctx.get("oi_change_pct"),
            "rsi_4h":               ctx.get("rsi_4h"),
            "rsi_1h":               ctx.get("rsi_1h"),
            "bb_breach_ratio":      ctx.get("bb_breach_ratio", ctx.get("bb_breach")),
            "btc_regime":           ctx.get("btc_regime"),
            "btc_regime_score":     ctx.get("btc_regime_score"),
            "is_blue_chip":         ctx.get("is_blue_chip"),
            "manipulation_detected": ctx.get("manipulation_detected"),
            "volume_24h":           ctx.get("volume_24h"),
            "market_cap":           ctx.get("market_cap"),
            "model_used":           MODEL,
            "daily_count":          self._daily_count,
            "analysis_text":        analysis,
            # Campos para post-mortem futuro (Vol II §11)
            "outcome_resolved":     False,
            "outcome_pnl":          None,
            "outcome_notes":        None,
        }
        log_path = LOGS_DIR / LOG_FILE
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            log.info("[DeepDive] Log salvo: %s", log_path)
        except Exception as exc:
            log.error("[DeepDive] Erro ao salvar log: %s", exc)
        return entry

    # ── Status e histórico (endpoints do dashboard) ───────────────────────────

    def get_status(self) -> dict:
        """Retorna estado atual do módulo para GET /api/deep-dive/status."""
        now = datetime.utcnow()
        return {
            "enabled":        bool(self._api_key),
            "disabled_until": (
                self._disabled_until.isoformat()
                if self._disabled_until and now < self._disabled_until
                else None
            ),
            "daily_count":    self._daily_count,
            "daily_max":      MAX_DAILY_ANALYSES,
            "daily_remaining": max(0, MAX_DAILY_ANALYSES - self._daily_count),
            "active_cooldowns": {
                sym: {
                    "last_analysis": ts.isoformat(),
                    "available_at":  (ts + timedelta(hours=COOLDOWN_HOURS)).isoformat(),
                }
                for sym, ts in self._cooldowns.items()
                if (now - ts).total_seconds() < COOLDOWN_HOURS * 3600
            },
            "model":                MODEL,
            "cost_per_analysis":    "~$0.003-0.005",
            "monthly_cost_estimate": f"~${self._daily_count * 30 * 0.004:.2f}",
        }

    def get_history(self, limit: int = 20) -> list:
        """Retorna últimos deep dives do log para GET /api/deep-dive/history."""
        log_path = LOGS_DIR / LOG_FILE
        if not log_path.exists():
            return []
        entries = []
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            return []
        return entries[-limit:]
