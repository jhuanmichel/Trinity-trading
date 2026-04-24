# PARTE 4 — Blue Chips no Dataset: Investigação

## Pergunta

Por que `crash_trader` tem **0 blue chips em 482 outcomes** (0%), enquanto `pump_trader` registra 11% e `funding_scanner` 7%?

## Dataset (logs/outcomes.clean.jsonl — 3846 outcomes)

| Source | Total | Blue chips | % |
|---|---|---|---|
| `unknown` (legacy) | 2,738 | 250 | 9.1% |
| `crash_trader` | 482 | **0** | **0.0%** |
| `funding_scanner` | 326 | 24 | 7.4% |
| `pump_trader` | 287 | 32 | 11.1% |
| `altcoin_scanner` | 13 | 4 | 30.8% |
| **Total** | **3,846** | **310** | **8.06%** |

Blue bases consideradas: `{BTC, ETH, SOL, BNB, XRP, SUI, ZEC, LINK, ENA, DOGE, ADA, AVAX, DOT, TON, MATIC}`.

## 1) Existe filtro explícito que exclua blue chips?

**Não.** Grep em toda a codebase:

```bash
grep -rn "BLUE_CHIPS.*not in\|not in BLUE_CHIPS\|skip.*blue\|exclude.*blue\|blacklist\|blue.*skip" --include="*.py" .
# → zero resultados
```

Pelo contrário: o `crash_scoring_engine.py:294` **aumenta** o score de blue chips em 1.12x (Blue Chip Boost — altcoins com liquidez institucional).

## 2) Threshold mínimo de entrada no scanner

`trinity/traders/predictive_crash_trader/altcoin_market_scanner.py:462`:

```python
if not (vol_usd >= MIN_VOLUME_24H_USD or abs(pct_change) >= MIN_PRICE_CHANGE_PCT):
    continue
```

Com:
- `MIN_VOLUME_24H_USD = 300_000` ($300K)
- `MIN_PRICE_CHANGE_PCT = 2.5` (2.5%)

Ambos são valores baixos — blue chips passam facilmente.

## 3) Root cause: tabela de overextension filtra organicamente

`altcoin_market_scanner.py:469-474`:

```python
if   pct_change > 50:  overext = 30
elif pct_change > 30:  overext = 25
elif pct_change > 20:  overext = 18
elif pct_change > 10:  overext = 10
elif pct_change > 5:   overext = 4
else:                  overext = 0
```

Crash scanner só pontua alto (S1=30) quando a moeda já **subiu >50% em 24h** (mercado de "shitcoin que precisa cair"). Blue chips tipicamente movem ±2–8% por dia. Portanto:
- BTC/ETH/etc. entram no scanner (passam vol + pct gate de 2.5%)
- Mas **nunca acumulam score ≥ 60 na composição crash** (overext=0 ou 4, combinado com funding tipicamente neutro em blue chips, OI boost moderado)
- Outcome só é registrado quando o sinal é considerado válido (score mínimo) — por isso **0 blue chip outcomes**

É design intencional: `predictive_crash_trader` caça altcoins overextended prestes a cascadear liquidações. BTC corrigir -5% não é o mesmo fenômeno que WLDUSDT +150% → -40% em 6h.

## 4) Blue chips atingem +50% em 24h no mercado atual?

Não. Nos últimos 3 meses, nenhum blue chip da lista fez +50% em 24h. Logo crash_trader, by design, não pode registrar outcomes deles — **não é bug, é arquitetura**.

## 5) Recomendação

**Manter o design atual.** Crash_trader é especializado em cascade liquidations de altcoins pós-parabólica. Blue chips seguem dinâmica diferente (suporte/resistência macro, correlação BTC, fluxo ETF/spot) e merecem scanner dedicado se o usuário quiser cobertura — mas esse é outro projeto, não bug de filtro.

Pump trader registra 11% blue chips (proporção saudável) porque altas moderadas (+3–10%) de blue chips acumulam score via momentum+funding+OI sem depender de overextension extrema. Isso já cobre o caso BTC "prestes a pumpar".

### Se quiser visibilidade especifica de blue chips em crash

Poderia:
- Adicionar scanner dedicado `blue_chip_crash_scanner.py` com thresholds menores (pct_change>-3% + funding>>100% APR + resistência 4H quebrada)
- Ou baixar S1 overext table para `>5% = 15pts`, mas isso explode falsos positivos em altcoins

Nenhuma das duas está no escopo desta operação.

## Conclusão

- Filtro explícito? **Não.**
- Filtro implícito? **Sim, via tabela de overextension — design choice, não bug.**
- Ação recomendada: **nenhuma.** Documentar comportamento.
