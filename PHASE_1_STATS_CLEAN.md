# Phase 1 Stats (clean) — WR recalculada

**Source**: `logs/outcomes.clean.jsonl`
**Total outcomes**: 3,846

## WR por direction
| Direction | WR | W | L | Total |
|---|---|---|---|---|
| LONG | 32.4% | 686 | 1431 | 2372 |
| SHORT | 53.0% | 451 | 400 | 1474 |

## WR por direction × tier (normalized_tier, n ≥ 10)
| Direction | Tier | WR | W | L | Total |
|---|---|---|---|---|---|
| LONG | `UNKNOWN` | 41.2% | 63 | 90 | 186 |
| LONG | `EXTREME` | 38.1% | 64 | 104 | 247 |
| LONG | `MICRO` | 37.6% | 103 | 171 | 297 |
| LONG | `TRADEABLE` | 36.1% | 26 | 46 | 84 |
| LONG | `STRONG` | 32.8% | 22 | 45 | 84 |
| LONG | `WEAK` | 31.7% | 84 | 181 | 283 |
| LONG | `HIGH` | 29.0% | 324 | 792 | 1189 |
| SHORT | `MICRO` | 67.0% | 138 | 68 | 451 |
| SHORT | `HIGH` | 58.2% | 173 | 124 | 436 |
| SHORT | `EXTREME` | 52.4% | 11 | 10 | 35 |
| SHORT | `TRADEABLE` | 46.2% | 6 | 7 | 84 |
| SHORT | `WEAK` | 39.2% | 122 | 189 | 465 |

## WR por source
| Source | WR | W | L | Total |
|---|---|---|---|---|
| `unknown` | 39.8% | 905 | 1368 | 2738 |
| `crash_trader` | 36.2% | 42 | 74 | 482 |
| `funding_scanner` | 39.0% | 127 | 199 | 326 |
| `pump_trader` | 24.6% | 59 | 181 | 287 |
| `altcoin_scanner` | 30.8% | 4 | 9 | 13 |

## WR por symbol (top 20 por volume)
| Symbol | WR | W | L | Total |
|---|---|---|---|---|
| `WLDUSDT` | 29.9% | 76 | 178 | 254 |
| `COMPUSDT` | 10.2% | 21 | 184 | 231 |
| `XIONUSDT` | 50.0% | 93 | 93 | 186 |
| `XMRUSDT` | 54.4% | 31 | 26 | 185 |
| `HIGHUSDT` | 38.9% | 51 | 80 | 148 |
| `CHIPUSDT` | 21.7% | 23 | 83 | 106 |
| `APTUSDT` | 90.5% | 76 | 8 | 90 |
| `EDUUSDT` | 43.4% | 36 | 47 | 83 |
| `ALICEUSDT` | 33.8% | 23 | 45 | 68 |
| `ICPUSDT` | 85.4% | 41 | 7 | 67 |
| `TAOUSDT` | 3.0% | 2 | 65 | 67 |
| `RAVEUSDT` | 31.0% | 18 | 40 | 66 |
| `AVAXUSDT` | 27.0% | 17 | 46 | 65 |
| `FLORKUSDT` | 36.5% | 23 | 40 | 63 |
| `GRIFFAINUSDT` | 18.2% | 10 | 45 | 55 |
| `DOTUSDT` | 4.3% | 2 | 45 | 49 |
| `PEPEUSDT` | 34.9% | 15 | 28 | 46 |
| `RDNTUSDT` | 26.8% | 11 | 30 | 42 |

## WR: SUSPECT boost vs CLEAN
| Group | Direction | WR | W | L | Total |
|---|---|---|---|---|---|
| *(suspect=0 no dataset atual — sem campo `boosts` nos outcomes legado)* | - | - | - | - | - |

## WR: Blue chip vs resto
| Group | WR | W | L | Total |
|---|---|---|---|---|
| BLUE_CHIP | 28.1% | 43 | 110 | 163 |
| ALTCOINS | 38.9% | 1094 | 1721 | 3683 |
