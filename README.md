# 🤖 Crypto Agent — IA Analítica para Futuros MEXC

Sistema de análise probabilística com 9 módulos para geração de sinais
de LONG / SHORT / NO TRADE com alertas automáticos no Telegram.

## Arquitetura

```
MEXC API ──────────────────────────────────────────────┐
                                                        ▼
                          ┌─────────────────────────────────────────┐
                          │           MÓDULOS DE ANÁLISE            │
                          │  1. Regime     (ADX, BB Width, ATR)     │
                          │  2. Tendência  (EMA, MACD, Ichimoku)    │
                          │  3. Momentum   (RSI, Stoch, CCI)        │
                          │  4. Volume     (OBV, VWAP, CMF)         │
                          │  5. Derivativos(OI, Funding, L/S)       │
                          │  6. On-chain   (Flows, MVRV)            │
                          │  7. Sentimento (Fear & Greed)           │
                          └──────────────┬──────────────────────────┘
                                         ▼
                          ┌──────────────────────────────┐
                          │   Score Probabilístico       │
                          │   LONG: X% | SHORT: Y%       │
                          └──────────────┬───────────────┘
                                         ▼
                          ┌──────────────────────────────┐
                          │   Claude AI (análise final)  │
                          │   entrada / stop / alvos     │
                          └──────────────┬───────────────┘
                                         ▼
                          ┌──────────────────────────────┐
                          │   Telegram Alert             │
                          │   📲 Sinal completo          │
                          └──────────────────────────────┘
```

## Setup em 5 passos

### 1. Instalar dependências
```bash
pip install -r requirements.txt
```

### 2. Configurar API keys
```bash
cp .env.example .env
# Edite o .env com suas chaves
```

APIs necessárias:
| API | Para quê | Gratuito? |
|-----|----------|-----------|
| MEXC | Dados de preço e OHLCV | ✅ Sim |
| Anthropic (Claude) | Análise de IA | Pago (por uso) |
| Telegram Bot | Alertas | ✅ Sim |
| Fear & Greed | Sentimento | ✅ Sim (sem key) |
| Coinglass | OI, Funding, L/S Ratio | ✅ Plano free |
| Glassnode | Dados on-chain | ✅ Plano free |

### 3. Criar bot no Telegram
1. Fale com @BotFather → `/newbot`
2. Copie o TOKEN gerado para o .env
3. Fale com @userinfobot para pegar seu CHAT_ID

### 4. Testar conexão
```bash
python mexc_client.py    # Testa conexão MEXC
```

### 5. Rodar o agente
```bash
python main.py
```

## Estrutura de Arquivos

```
crypto_agent/
├── .env.example          # Template de variáveis
├── config.py             # Configurações centralizadas
├── mexc_client.py        # Dados MEXC (OHLCV, order book)
├── scoring.py            # Motor de score probabilístico
├── agent.py              # Integração Claude AI
├── alerts.py             # Alertas Telegram
├── main.py               # Orquestrador principal
├── requirements.txt      # Dependências Python
├── indicators/
│   ├── regime.py         # Módulo 1: Regime de mercado
│   ├── trend.py          # Módulo 2: Tendência
│   ├── momentum.py       # Módulo 3: Momentum
│   ├── volume.py         # Módulo 4: Volume
│   ├── derivatives.py    # Módulo 5: Derivativos
│   ├── onchain.py        # Módulo 6: On-chain
│   └── sentiment.py      # Módulo 7: Sentimento
└── logs/                 # Histórico de sinais (JSON)
```

## Pesos do Score (configurável em config.py)

| Módulo | Peso |
|--------|------|
| Regime | 10% |
| Tendência | 30% |
| Momentum | 20% |
| Volume | 15% |
| Derivativos | 15% |
| On-chain | 5% |
| Sentimento | 5% |

## Lógica de Sinal

```
Score > 75  → LONG (alta confiança)
Score > 65  → LONG (média confiança)
Score < 25  → SHORT (alta confiança)
Score < 35  → SHORT (média confiança)
ADX < 20    → preferência por NO TRADE (mercado lateral)
```

## ⚠️ Aviso

Este sistema gera **análises e alertas**, não executa ordens automaticamente.
Sempre use gestão de risco. Criptomoedas são altamente voláteis.
Não é recomendação de investimento.
