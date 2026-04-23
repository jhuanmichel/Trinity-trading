# Phase 2 Regression — V1 implied multiplier vs V2 caps

**Source**: `logs/outcomes.clean.jsonl`
**Total outcomes**: 3,846
**Utilizaveis (com layer_scores)**: 3,660
**Skipped (sem layers)**: 186  |  **(base zero)**: 0

**V2 caps**: individual=1.3, total=1.8

## Distribuicao v1_implied_mult = score / sum(layer_scores)
| Estatistica | Valor |
|---|---|
| min    | 0.877 |
| p10    | 1.000 |
| p25    | 1.000 |
| p50    | 1.246 |
| mean   | 1.255 |
| p75    | 1.399 |
| p90    | 1.702 |
| p95    | 2.121 |
| max    | 2.694 |

## Buckets de v1_implied_mult
| Bucket | N | % |
|---|---|---|
| `<1.00 (penalty)` | 23 | 0.6% |
| `1.00-1.10` | 1,680 | 45.9% |
| `1.10-1.30` | 800 | 21.9% |
| `1.30-1.50` | 481 | 13.1% |
| `1.50-1.80` | 337 | 9.2% |
| `1.80-2.50 (V2 capped)` | 338 | 9.2% |
| `>=2.50 (V2 heavy cap)` | 1 | 0.0% |

**Outcomes V1 com mult > 1.8 (capped em V2)**: 339 / 3,660 (9.3%)

## Por source
| Source | N | mean_mult | p50 | p95 | max | %>1.80 |
|---|---|---|---|---|---|---|
| `unknown` | 2,565 | 1.227 | 1.000 | 1.882 | 2.694 | 8.7% |
| `crash_trader` | 482 | 1.530 | 1.350 | 2.127 | 2.131 | 24.3% |
| `funding_scanner` | 326 | 1.000 | 1.000 | 1.000 | 1.000 | 0.0% |
| `pump_trader` | 287 | 1.335 | 1.373 | 1.502 | 1.678 | 0.0% |

## Interpretacao

`v1_implied_mult = outcome.score / sum(layer_scores)` revela o multiplicador efetivo que V1 aplicou sobre os 4 componentes base (0-25 cada).

- **mult < 1.0**: V1 aplicou floor/normalization ou os componentes foram reduzidos (ex: min(100, score) apos boost).
- **mult em 1.0-1.30**: regime normal; V2 reproduz.
- **mult em 1.30-1.80**: V1 aplicou boosts moderados; V2 mantem dentro do cap total.
- **mult > 1.80 (339 outcomes, 9.3%)**: V1 encadeou multiplicadores alem do cap total V2 — estes sao os casos onde V2 atenua o score explicitamente.

## GATE PASS: 9.3% outcomes capped em V2 (<= 20%). Mudanca localizada a outliers.
