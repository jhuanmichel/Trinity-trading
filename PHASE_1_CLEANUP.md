# Phase 1 Cleanup — Dataset Outcomes

**Input**: `/tmp/outcomes_prod_export.jsonl` — 4,171 outcomes
**Output**: `logs/outcomes.clean.jsonl` — 3,846 outcomes
**Retention**: 92.2%

## Filtros aplicados
| Categoria | N |
|---|---|
| Filtrados sem symbol | 0 |
| Filtrados sem status | 0 |
| Duplicatas removidas | 325 |

## Transformações
| Categoria | N |
|---|---|
| Symbols normalizados | 1,673 |
| Tiers corrompidos → UNKNOWN | 186 |
| Suspect boost flagged (>1.5x) | 0 |

## Distribuição tier (clean)
| Tier | N |
|---|---|
| `HIGH` | 1,625 |
| `MICRO` | 748 |
| `WEAK` | 748 |
| `EXTREME` | 282 |
| `UNKNOWN` | 186 |
| `TRADEABLE` | 168 |
| `STRONG` | 87 |
| `CRITICAL` | 2 |

## Distribuição source (clean)
| Source | N |
|---|---|
| `unknown` | 2,738 |
| `crash_trader` | 482 |
| `funding_scanner` | 326 |
| `pump_trader` | 287 |
| `altcoin_scanner` | 13 |

## Top 20 symbols (clean, normalizados)
| Symbol | N |
|---|---|
| `WLDUSDT` | 254 |
| `COMPUSDT` | 231 |
| `XIONUSDT` | 186 |
| `XMRUSDT` | 185 |
| `HIGHUSDT` | 148 |
| `CHIPUSDT` | 106 |
| `APTUSDT` | 90 |
| `EDUUSDT` | 83 |
| `ALICEUSDT` | 68 |
| `ICPUSDT` | 67 |
| `TAOUSDT` | 67 |
| `RAVEUSDT` | 66 |
| `AVAXUSDT` | 65 |
| `FLORKUSDT` | 63 |
| `OBORTECHUSDT` | 57 |
| `GRIFFAINUSDT` | 55 |
| `PPTAIUSDT` | 50 |
| `DOTUSDT` | 49 |
| `PEPEUSDT` | 46 |
| `RDNTUSDT` | 42 |

## ✅ GATE PASS: retention 92.2% ≥ 90%
