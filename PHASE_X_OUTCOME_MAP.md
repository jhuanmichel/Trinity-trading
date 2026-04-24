# Outcome Registration Map — Phase X (Pre/Post Sampling)

## Current state (pre-X)

| Source | Registered threshold | Gate |
|---|---|---|
| `predictive_pump_trader` | `opportunity_score >= OPP_THRESHOLD (35)` | score >= 35, 100% |
| `predictive_crash_trader` | `opportunity_score >= OPP_THRESHOLD (35)` | score >= 35, 100% |
| `funding_extreme_scanner` | `composite_score >= ALERT_THRESHOLD` | composite >= 55 |
| `market_movers` | Tier detectado (ALERT/URGENT/PARABOLIC) | no score gate |
| `full_market_scanner` | Quando alerta é emitido (tier in ALERT/CRITICAL/...) | tier gate |

**Problema:** 100% dos 35-79 entram no dataset. Volume alto em banda que pouco informa ML.

## Post-X (sampling estratificado aplicado)

| Source | Mudança |
|---|---|
| `predictive_pump_trader:319` | `score >= 35` → `OutcomeSampler.should_register(score)`. Todos candidatos ganham `score_bucket`. |
| `predictive_crash_trader:363` | idem |
| `funding_extreme_scanner` | Não tocado (composite_score >= 55 é threshold de alerta, não registro amplo; mudança invasiva desnecessária) |
| `market_movers` | Não tocado (já muted no Telegram; outcomes registrados são gated por tier de manipulação já conservador) |
| `full_market_scanner` | Não tocado (gating tier, não por score) |

### OutcomeSampler buckets

| Score range | Rate | Label |
|---|---|---|
| `>= 80` | 100% | `alert` |
| `60-79` | 30% | `near` |
| `35-59` | 10% | `low` |
| `< 35` | 0% | `skip` |

Sampling determinístico (hash `signal_id` via md5) — mesmo sinal sempre cai no mesmo lado, permite replay.

### Campos novos persistidos

`score_bucket` na entrada pending + no outcome resolvido. Valores possíveis:
- `alert`: score >= 80 (100% registrado)
- `near`: score 60-79 passou pelo sampling (30%)
- `low`: score 35-59 passou pelo sampling (10%)
- Outros labels ficam só como debug log, não persistem (dado que o sample-out não registra).

## Impacto esperado

Volume pré-X em pump+crash: ~1600 outcomes/dia nas bandas 35+.
Volume pós-X:
- 80+: todos (~15% do funil, inalterado)
- 60-79: 30% (era 100% — redução 70%)
- 35-59: 10% (era 100% — redução 90%)

Total outcomes esperado: ~30-40% do pré-X. Disco +30% menos denso, mas representativo por bandas.

ML: pode agora segmentar análise por `score_bucket` → identifica se score 60-79 é preditivo ou só ruído, sem precisar do 100% do tráfego.
