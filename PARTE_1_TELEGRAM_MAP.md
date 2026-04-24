# PARTE 1 — TELEGRAM SENDERS MAP

## Módulos que enviam Telegram (mapeados via grep)

| Módulo | Função/método | Flag local | Guard aplicado |
|---|---|---|---|
| `trinity/traders/predictive_pump_trader/predictive_pump_trader.py` | `_send_telegram_alerts` (async loop) → `_send_pump_telegram` | — | ✅ FuturesGuard before `_send_pump_telegram` |
| `trinity/traders/predictive_crash_trader/predictive_crash_trader.py` | `_send_telegram_alerts` (async loop) → `_send_crash_telegram` | — | ✅ FuturesGuard before `_send_crash_telegram` |
| `trinity/modules/parabolic_scanner.py` | `_send_parabolic_alert` (inline, após loop de filtro) | — | ✅ FuturesGuard before `_send_parabolic_alert` |
| `trinity/modules/market_movers.py` | `send_message` (via `alerts.py`) | `MARKET_MOVERS_TELEGRAM_ENABLED = False` (linha 31) | ✅ Muted (shadow; outcomes ainda registrados) |
| `trinity/modules/deep_dive_trigger.py` | `_send_telegram_sync` (via `requests.post`) | — | ✅ Upstream-guarded (só chamado pós parabolic/crash, ambos guarded) |
| `trinity/traders/funding_extreme_scanner.py` | `_send_funding_telegram` (função definida mas DEAD CODE — zero callers) | — | n/a (não executa) |
| `trinity/traders/daily_summary.py` | `requests.post` direto para `sendMessage` | — | n/a (resumo diário, não é alerta de sinal) |
| `main.py` | `alerts.send_message`, `alerts.send_signal`, `alerts.send_institutional_signal`, `alerts.send_error` | — | n/a (SYMBOL fixo = BTC/USDT:USDT; BTC sempre tem futures + volume >> $10M) |
| `news_sentinel.py` | `_send_telegram_alert` (comentada, linha 254) | — | n/a (desligado) |
| `dashboard/server.py:2320` | `sendMessage` via `/api/deep-dive/telegram-test` | — | n/a (endpoint de teste manual) |

## Sequência: alerta vs registro de outcome

Para pump/crash traders: **outcome registrado ANTES do alerta**, via `register_signal(...)` no caller `run_scan()`. O FuturesGuard bloqueia só o envio Telegram; outcome continua entrando no histórico para análise ML.

Para parabolic scanner: similar — outcome em `scanner_state` registrado antes do `_send_parabolic_alert`. FuturesGuard bloqueia só o alerta.

## Threshold unificado

Carve-out `60 if symbol in BLUE_CHIPS else ALERT_THRESHOLD` removido em pump + crash traders (4 ocorrências). Threshold único = `ALERT_THRESHOLD` (80 default, configurável via env). Tier thresholds (HIGH/MID/LAUNCH) preservados — controlam força do alerta, não se alerta.

## FuturesGuard

- Cache TTL: 1h
- Exchanges consultadas: `["mexc", "binance", "bybit", "okx", "gateio"]` (prioridade)
- Apenas **MEXC** wireado hoje (único exchange que o bot usa ativamente)
- Min volume 24h: **$10M USD**
- Fail-closed: erro de fetch → bloqueia (evita spam em falhas de rede)
- Endpoint de visibilidade: `GET /api/futures-guard/stats`

## Diferença vs pré-fix

**Antes:** RAXUSDT SHORT score 76 passava (blue chips had threshold 60, non-blue had 80). FLORK PARABOLIC com $1.1M volume 24h passava (parabolic scanner só checava `MIN_VOLUME_USD = 500_000`).

**Depois:**
- Score mínimo = 80 uniformemente (nem RAX nem qualquer outro passa abaixo)
- FuturesGuard corta qualquer coin sem par futures ativo em nenhuma exchange prioritária
- FLORK com $1.1M vol 24h < $10M FuturesGuard → bloqueado
