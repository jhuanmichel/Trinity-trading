# Phase 1 Audit — Dataset Outcomes

**Source**: `/tmp/outcomes_prod_export.jsonl`
**Total outcomes**: 4,171

## Distribuição por source
| Source | N | % |
|---|---|---|
| `unknown` | 2,782 | 66.7% |
| `crash_trader` | 697 | 16.7% |
| `funding_scanner` | 357 | 8.6% |
| `pump_trader` | 322 | 7.7% |
| `altcoin_scanner` | 13 | 0.3% |

## Distribuição por direction
| Direction | N | % |
|---|---|---|
| LONG | 2,461 | 59.0% |
| SHORT | 1,710 | 41.0% |

## Distribuição por status
| Status | N | % |
|---|---|---|
| LOSS | 1,901 | 45.6% |
| WIN | 1,149 | 27.5% |
| NEUTRAL | 1,121 | 26.9% |

## Distribuição por tier (top 20)
| Canônico | Tier | N | % |
|---|---|---|---|
| ✅ | `HIGH` | 1,671 | 40.1% |
| ✅ | `MICRO` | 874 | 21.0% |
| ✅ | `WEAK` | 828 | 19.9% |
| ✅ | `EXTREME` | 292 | 7.0% |
| ✅ | `TRADEABLE` | 222 | 5.3% |
| ✅ | `STRONG` | 96 | 2.3% |
| ❌ | `27` | 53 | 1.3% |
| ❌ | `37` | 42 | 1.0% |
| ❌ | `25` | 30 | 0.7% |
| ❌ | `15` | 18 | 0.4% |
| ❌ | `32` | 10 | 0.2% |
| ❌ | `INVALID` | 8 | 0.2% |
| ❌ | `17` | 7 | 0.2% |
| ❌ | `30` | 7 | 0.2% |
| ❌ | `42` | 3 | 0.1% |
| ❌ | `22` | 3 | 0.1% |
| ✅ | `CRITICAL` | 2 | 0.0% |
| ❌ | `39` | 2 | 0.0% |
| ❌ | `20` | 2 | 0.0% |
| ❌ | `24` | 1 | 0.0% |

## Formatos de symbol
| Formato | N | % | Exemplo |
|---|---|---|---|
| compact | 2,312 | 55.4% | `ONTUSDT` |
| underscore | 1,673 | 40.1% | `COMP_USDT` |
| other | 186 | 4.5% | `ARB` |

## Blue chips no dataset
**Total blue chip**: 167 (4.00% do dataset)

| Symbol | N |
|---|---|
| `AVAXUSDT` | 65 |
| `DOTUSDT` | 50 |
| `LINKUSDT` | 19 |
| `SUIUSDT` | 11 |
| `DOGEUSDT` | 9 |
| `XRPUSDT` | 7 |
| `BNBUSDT` | 3 |
| `SOLUSDT` | 3 |

## Problemas detectados
| Categoria | Count | % |
|---|---|---|
| Tier corrompido (não-canônico) | 186 | 4.46% |
| Symbol ausente | 0 | 0.00% |
| Status ausente | 0 | 0.00% |
| Score fora [0-150] | 0 | 0.00% |
| Duplicados signal_id | 269 | 6.45% |
| Duplicados composite | 293 | 7.02% |
| Multiplicadores >1.5x | 0 | 0.00% |
| Sem dados de boost | 4,171 | 100.00% |

### Exemplos tier corrompido
| Symbol | Tier | Source |
|---|---|---|
| `ARB` | `25` | `unknown` |
| `DOT` | `25` | `unknown` |
| `INJ` | `25` | `unknown` |
| `ZEC` | `37` | `unknown` |
| `AVAX` | `37` | `unknown` |

