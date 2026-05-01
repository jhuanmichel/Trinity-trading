# 🤖 Trinity Trading — IA Analítica para Futuros MEXC

Sistema de análise probabilística multi-camadas para futuros perpétuos de
criptomoedas. Combina ~17 engines (técnicos, on-chain, derivativos, ML,
geopolítico, ciclo BTC) num **Trinity Score** unificado e dispara alertas
no Telegram. Inclui dashboard web, scanners paralelos de pump/crash, sistema
de outcome tracking e pipeline de ML.

> Este sistema gera **análises e alertas**, não executa ordens. Sempre use
> gestão de risco. Não é recomendação de investimento.

## Arquitetura

```
                       MEXC + Binance + Coinglass + Glassnode + RSS macro
                                            │
                                            ▼
   ┌──────────────────── ANÁLISE INSTITUCIONAL (a cada 60min) ────────────────────┐
   │ 1. Regime / ATR / BB / ADX                                                   │
   │ 2. Trend (EMA, MACD, Ichimoku)                                               │
   │ 3. Momentum (RSI, Stoch, CCI)                                                │
   │ 4. Volume / CVD                                                              │
   │ 5. Derivativos (Funding, OI, L/S)                                            │
   │ 6. Liquidações (Coinglass + WS Binance ao vivo)                              │
   │ 7. Correlação macro (BTC.D, USDT.D, ETH)                                     │
   │ 8. Score Institucional (6 camadas, 100pts)                                   │
   │ 9. MTF (1m / 5m / 15m / 1h)                                                  │
   │ 10. Smart Money Concepts (BOS, CHoCH, OB, FVG, MTF SMC)                      │
   │ 11. Market Maker Engine (sweeps, traps, premium/discount Fibonacci)          │
   │ 12. Pressure Meter (IPM, -100..+100, filtro |40|)                            │
   │ 13. Rare Setup Detector (OB+FVG+Sweep, Sweep+Liq+MTF…)                       │
   │ 14. Geopolitical Intelligence (RSS + Claude haiku)                           │
   │ 15. Bitcoin Cycle Engine (halving, MVRV, on-chain)                           │
   │ 16. Institutional Direction Engine (delta, perp/spot, MM defense)            │
   │ 17. Neural Intelligence v2 (MLP + LSTM + CNN + Transformer + ensemble)       │
   └──────────────────────────────┬───────────────────────────────────────────────┘
                                  ▼
                  ┌──────────────────────────────────┐
                  │  Trinity Score v6                 │
                  │  inst×0.22 + |IPM|×0.13 +         │
                  │  rare×0.10 + geo×0.10 +           │
                  │  cycle×0.10 + dir×0.15 +          │
                  │  neural×0.20                      │
                  └────────────────┬─────────────────┘
                                  ▼
       ┌───── Sazonalidade ────► Funding Gate ────► HCF ────► News Clearance ─────┐
       │                                                                          │
       │              gates sequenciais — qualquer um bloqueia                    │
       └────────────────────────────┬─────────────────────────────────────────────┘
                                  ▼
            ┌──────────────────────────────────────────────────┐
            │  Telegram • Outcome Tracker • Dashboard JSON     │
            └──────────────────────────────────────────────────┘
```

Em paralelo, rodam:
- **Full Market Scanner** — varre ~300+ contratos MEXC a cada 90s, aplica
  6 detectores (D1–D6: funding/OI/volume/CVD/liquidez/compressão), dispara
  alertas tier CRÍTICO/ALTO/MÉDIO.
- **Pump Radar / Crash Radar** — `trinity/traders/predictive_*` analisam
  altcoins com modelos próprios de squeeze, gravidade, breakout (pump) e
  cascade liquidation, whale dump (crash). A cada 30min.
- **Parabolic Scanner** (15min), **Market Movers** (10min), **News Sentinel**
  (2min), **BTC Liquidations WS Binance** (tempo real).

## Setup

### 1. Dependências

```bash
pip install -r requirements.txt
```

Python 3.11+ obrigatório (ver `runtime.txt`).

### 2. Variáveis de ambiente

```bash
cp .env.example .env
# preencher chaves
```

| Variável | Para quê | Custo |
|---|---|---|
| `MEXC_API_KEY` / `MEXC_SECRET_KEY` | OHLCV, ticker, orderbook | Grátis |
| `ANTHROPIC_API_KEY` | agent.py (Claude opus), morning brief, news classifier | Pago por uso |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | Alertas | Grátis |
| `COINGLASS_API_KEY` | Funding, OI, L/S, liquidações | Free tier |
| `CRYPTOQUANT_API_KEY` | On-chain derivado | Free tier |
| `GLASSNODE_API_KEY` | On-chain | Free tier |
| `FRED_API_KEY` | Macro (Fed) | Grátis |

Parâmetros de comportamento: `SYMBOL`, `TIMEFRAME`, `SCORE_THRESHOLD`,
`INST_SCORE_THRESHOLD`, `INST_INTERVAL_MINUTES`, `SCORING_V2_ENABLED`,
`SCORING_V2_LIVE` — defaults em `config.py`.

### 3. Bot Telegram

1. `@BotFather` → `/newbot` → copie o TOKEN
2. `@userinfobot` → copie seu CHAT_ID
3. Cole no `.env`

### 4. Frontend (opcional)

```bash
cd frontend
npm ci
npm run build      # → dashboard/static/app/
```

Já buildado em produção via Render build hook.

### 5. Rodar

**Local (apenas scheduler de análise):**
```bash
python main.py
```

**Local (scheduler + dashboard FastAPI na :8000):**
```bash
python start.py
```

**Render (produção):** push para `main` dispara deploy via
`.github/workflows/deploy.yml`. `start.py` sobe scheduler em thread daemon
e uvicorn para o dashboard. Persistent Disk montado em `/data` para
outcomes sobreviverem a redeploys.

## Score & Pesos

### Score Institucional (6 camadas, 100pts) — `institutional_scoring.py`

| Camada | Peso |
|---|---|
| Market Structure | 20 |
| Liquidity (deriv + liq) | 20 |
| Volume | 20 |
| Trend | 20 |
| Correlation | 10 |
| Volatility | 10 |

Válido só com **≥3 confluências** alinhadas (Invincible Mode).

### Trinity Score v6 (composição final)

```
trinity = inst×0.22 + pressure_norm×0.13 + rare_norm×0.10
        + geo×0.10 + cycle×0.10 + direction×0.15 + neural×0.20
```

`pressure_norm` e `rare_norm` remapeados para [50, 100] (50 = neutro).

### Score Probabilístico legado (`scoring.py`) — usado por `run_analysis_signal`

| Módulo | Peso |
|---|---|
| Trend | 25 |
| Momentum | 15 |
| Regime | 10 |
| Volume | 10 |
| Derivativos | 15 |
| Liquidações | 15 |
| On-chain | 5 |
| Sentimento | 5 |

## Lógica de sinal final

Sequência de gates antes do Telegram:

1. `inst.valid == True` (≥3 confluências)
2. Estrutura ≠ LATERAL/INDEFINIDA/TRANSIÇÃO
3. `trinity_score_seasonal ≥ INST_SCORE_THRESHOLD` (default 60)
4. **Funding Gate** (`FundingRateManager`): score ≥ 0
5. **HCF** (`HighConvictionFilter`): MTF aligned + BOS 4H + volume + OB/funding
6. **News Clearance**: macro não está em lock high-impact

Cada gate bloqueia → escreve estado no dashboard mas não envia Telegram.

## Outcome Tracking & ML

- `outcome_tracker.py` registra cada sinal em `pending_outcomes.jsonl`
  (persistent disk em `/data/logs/`)
- Resolve outcomes via candles MEXC Futures: TP1_HIT/TP2_HIT (WIN),
  STOP_HIT (LOSS), EXPIRED após 48h (NEUTRAL)
- Atualiza `dashboard/win_rate.json` com win rate, breakdown por tier,
  best direction
- `weight_optimizer.py` analisa correlação layer→outcome e gera relatório
  diário (`dashboard/optimization_report.json`) — **read-only por design**:
  pesos sugeridos não são aplicados automaticamente
- Pipeline ML em `trinity/ml/` (MLManager + feature_importance +
  walk_forward + monte_carlo) escreve em `dashboard/ml_*.json`

## Schedules

| Job | Intervalo | Origem |
|---|---|---|
| `run_analysis_summary` | 120min | scheduler |
| `run_analysis_signal` | 240min | scheduler |
| `run_institutional_analysis` | 60min | scheduler |
| `run_pump_radar` / `run_crash_radar` | 30min | scheduler |
| Full Market Scanner | 90s | start.py |
| Parabolic Scanner | 15min | start.py |
| Market Movers | 10min | start.py |
| Outcome Health Monitor | 1h | start.py |
| Optimization Report | diário 00:05 UTC | start.py |
| Morning Brief PDF | diário 08:00 BRT | main.py |
| Cycle Intelligence PDF | segunda 08:05 | main.py |
| Weekly Digest | domingo 20:00 UTC | start.py |
| Backtest walk-forward | 30 dias | start.py |
| Recálculo sazonalidade | 30 dias | start.py |
| News Sentinel cycle | 2min | server.py async |
| BTC Liquidations WS | contínuo | btc_liquidation_engine |
| Keepalive externo | 55min | GitHub Actions |

## Estrutura de pastas

```
trading/
├── main.py                    # orquestrador principal
├── start.py                   # entrypoint produção (scheduler + uvicorn)
├── config.py                  # env + pesos
├── mexc_client.py             # ccxt wrapper
├── agent.py                   # Claude (sinal estruturado)
├── alerts.py                  # formatação Telegram (8 tipos de alerta)
├── scoring.py                 # score legado 7-camadas
├── institutional_scoring.py   # score 6-camadas
├── smart_money_engine.py      # SMC (BOS, OB, FVG, MTF)
├── market_maker_engine.py     # MM (sweeps, traps, premium/discount)
├── pressure_meter.py          # IPM (Cap. 4)
├── rare_setup_detector.py     # setups raros (Cap. 3)
├── btc_liquidation_engine.py  # WS Binance forceOrder
├── funding_rate_manager.py    # gate de funding
├── high_conviction_filter.py  # filtro confluência SMC
├── news_sentinel.py           # macro lock + multiplier
├── news_classifier.py         # Claude haiku
├── news_fetcher.py            # RSS + Nitter
├── seasonality_engine.py      # multiplier sazonal
├── session_filter.py          # janelas London/NY/Asia
├── outcome_tracker.py         # tracking WIN/LOSS
├── weight_optimizer.py        # correlação layer→outcome (read-only)
├── morning_brief.py           # PDF diário
├── cycle_intelligence.py      # PDF semanal
├── full_market_scanner.py     # scanner ~300+ contratos D1-D6
├── backtesting_engine.py      # walk-forward 4 janelas (2020-2024)
├── backtesting_smc_adapter.py # SMC offline
│
├── indicators/                # 10 módulos atômicos: regime, trend,
│                              # momentum, volume, derivatives,
│                              # liquidations, onchain, sentiment,
│                              # market_structure, correlation
│
├── trinity/
│   ├── core/                  # trinity_core, signal_consensus,
│   │                          # conflict_detector, confidence_engine,
│   │                          # scoring_engine, meta_learning
│   ├── traders/
│   │   ├── predictive_pump_trader/   # 8 detectores + scoring
│   │   ├── predictive_crash_trader/  # 8 detectores + scoring
│   │   ├── altcoin_scanner/
│   │   └── btc_regime_monitor, smart_entry_engine,
│   │       manipulation_detector, daily_summary,
│   │       funding_extreme_engine/scanner
│   ├── ml/                    # ml_manager, weight_optimizer,
│   │                          # feature_importance, walk_forward,
│   │                          # monte_carlo
│   ├── modules/               # parabolic_scanner, market_movers,
│   │                          # deep_dive_trigger, outcome_health_monitor
│   ├── scoring/               # boost_manager, engine_v2 (V2 shadow)
│   ├── exchanges/             # adapters mexc/binance/bybit/gateio/okx
│   ├── notifications/         # weekly_digest
│   ├── outcomes/              # tiers
│   ├── macro_report/          # PDF analítico
│   └── utils/                 # futures_guard, outcome_sampler
│
├── bitcoin_cycle_engine/      # 7 modelos (halving, MVRV, trend, on-chain…)
├── geopolitical_engine/       # RSS + Claude classifier
├── institutional_direction_engine/  # delta, perp/spot, MM defense
├── neural_engine/             # MLP, LSTM, CNN, Transformer + ensemble
├── backtester/                # backtest alternativo
├── altcoin-radar/             # webapp standalone
│
├── dashboard/
│   ├── server.py              # FastAPI (94KB)
│   ├── static/app/            # frontend Vite buildado
│   ├── reports/               # PDFs gerados
│   └── *.json                 # estado runtime (current_state, win_rate,
│                              # ml_results, optimization_report, …)
├── frontend/                  # source React + Vite
├── scripts/                   # diag, dataset_audit, fetch_historical,
│                              # calculate_seasonality, regression tests
├── tests/                     # pytest (backtest, tiers)
├── logs/                      # outcomes_*.jsonl rastreados,
│                              # signals/pending/rejected ignorados
└── .github/workflows/
    ├── deploy.yml             # push main → Render hook
    └── keepalive.yml          # /health + /api/run-now a cada 55min
```

## Tipos de alerta Telegram

| Função em `alerts.py` | Quando |
|---|---|
| `send_signal` | Score legado ≥ threshold (run_analysis_signal) |
| `send_institutional_signal` | Análise institucional aprovada (todos os gates) |
| `send_trinity_signal` | Trinity Core orquestrado (alert_priority HIGH/CRITICAL) |
| `send_summary` | Resumo periódico (com cooldown 6h) — atualmente desligado |
| `send_status_update` | Status sem sinal (cooldown 4h) — desligado |
| `send_message` | Genérico (FMS, scanners, news) |
| `send_error` | Falha crítica |

Há também alertas de PDF: Morning Brief (diário) e Cycle Intelligence (semanal).

## Documentos auxiliares

Pasta raiz contém auditorias técnicas em markdown:

- `PHASE_0_INVENTORY.md` — mapa do orquestrador + persistência
- `PHASE_1_AUDIT.md` / `_CLEANUP.md` / `_STATS_CLEAN.md` — análise do dataset
- `PHASE_2_REGRESSION.md` / `_SCORING_INVENTORY.md` — refactor V1→V2
- `PHASE_A_ML_INVENTORY.md` — diagnóstico do loop ML
- `PHASE_X_OUTCOME_MAP.md` — sampling estratificado de outcomes
- `PARTE_1_TELEGRAM_MAP.md` — mapa de senders + FuturesGuard
- `PARTE_4_FINDINGS.md` — análise de blue chips no dataset

## Deploy

`.github/workflows/deploy.yml` aciona `RENDER_DEPLOY_HOOK_URL` em push para
`main`. `render.yaml` define `buildCommand` (pip + Node 20 + npm build) e
`startCommand: python start.py` com `healthCheckPath: /health`.

`.github/workflows/keepalive.yml` previne o Render free tier de hibernar
(cron a cada 55min, ping `/health` + POST autenticado em `/api/run-now`).

## Aviso

Este sistema gera **análises e alertas**, não executa ordens automaticamente.
Sempre use gestão de risco. Criptomoedas são altamente voláteis. Não é
recomendação de investimento.
