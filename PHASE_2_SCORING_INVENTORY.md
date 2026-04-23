# Phase 2 Scoring Inventory

## Engines identificados

### `trinity/traders/predictive_pump_trader/pump_scoring_engine.py`
- Função principal: `score_pump(...)` (linha 73)
- Multiplicadores aplicados:
  - `squeeze_score` / `gravity_score` / `breakout_score` (cada um capped em 25, via `min(25.0, ... * 0.25)`) — linhas 92-94
  - `momentum_mult`: 1.25+ em função de `fund_ann_mom` (funding) + `pct_change_pump`, linhas ~200-220
  - BTC boost: presente (via `btc_regime` — linha ~234 comment "BTC boost → round")
  - DNA bonus via `_detect_pump_dna` (linha 334)
  - Funding extreme (analyze_funding_extreme) — aplica multiplicador
- Ordem observada (comentário linha 234): `funding_extreme → momentum → penalidade → DNA → BTC boost → round`
- Cap existente: `min(100.0, opportunity_score * momentum_mult)` aplicado one-shot (linha 251). Efeito combinado pode passar 100 antes do cap.

### `trinity/traders/predictive_crash_trader/crash_scoring_engine.py`
- Função principal: `score_crash(...)` (linha análoga)
- Multiplicadores:
  - `collapse_score / whale_score / vol_score` cada cap 25 (linhas 100-102)
  - `lev_amp = 1.0 + max(0.0, (ls_ratio - 1.5) * 0.25)` linha 157 — modesto
  - `overext_mult` escalonado (linhas 186-192):
    - `pct_change_24h > 150` → **1.70x**
    - `> 100` → 1.50x
    - `> 70` → 1.35x
    - `> 50` → 1.25x
    - `> 30` → 1.15x
    - `> 20` → 1.08x
  - `funding_extreme` via analyze_funding_extreme: até **2.50x** (tier CRITICAL) conforme `funding_extreme_engine.py:259-282`
  - BTC boost, DNA bonus (padrão similar ao pump)
- Aplicação: `opportunity_score = min(100.0, opportunity_score * overext_mult)` (linha 221) — one-shot por boost, permite inflação cumulativa

### `trinity/core/scoring_engine.py`
- Função: `calculate_score(...)` (linha 58)
- **Sem multiplicadores encadeados observados** em análise inicial. Usa `REGIME_MODIFIERS` (linhas 45-53) aplicando **1 modificador único** por market_regime. Não precisa de BoostManager refactor — é agregador ponderado de engines.
- **FORA DE ESCOPO** desta Fase 2.

### `trinity/traders/funding_extreme_engine.py`
- Função: `analyze_funding_extreme(coin_data, direction)` (linha 99 aprox)
- Retorna `composite_mult` de **1.0 a 2.50x** baseado em tier (CRITICAL/HIGH/ELEVATED/NORMAL).
- É o **MAIOR** multiplicador do sistema.
- Consumido por pump_scoring_engine e crash_scoring_engine.
- Desafio: score_pump_v2 precisa chamar analyze_funding_extreme e **passar o mult resultante** para ScoreBundle (capped em 1.30 individual).

## Catálogo de multiplicadores (V1 atual)

| Nome | Valor atual | Engine | Condição |
|---|---|---|---|
| `momentum_mult` (pump) | 1.25+ var | pump_scoring | `pct_change_pump` + `fund_ann_mom` combinação |
| `overext_mult` (crash) | 1.08 a **1.70** | crash_scoring | `pct_change_24h` tier (20/30/50/70/100/150%) |
| `funding_extreme.composite_mult` | **1.0 a 2.50** | funding_extreme_engine | tier funding rate (`>=85 composite → 2.50`) |
| `lev_amp` | 1.0 a ~1.25 | crash_scoring | `ls_ratio > 1.5` linear |
| DNA bonus | ~1.10 | ambos | `_detect_pump_dna` pattern match |
| BTC boost | varia | ambos | `btc_regime` alignment |

## Design V2 mapping (conservador)

- **momentum_mult** → `ScoreBundle.add_boost('momentum', momentum_mult, reason)` → capped 1.30 individual
- **overext_mult** → `add_boost('overextended', ...)` → capped 1.30 (estava 1.70)
- **funding_extreme** → `add_boost('funding_extreme', ..., reason)` → capped 1.30 (estava até 2.50)
- **lev_amp** → `add_boost('leverage_amp', ...)` → geralmente abaixo de 1.30, raramente capped
- **DNA/BTC** → `add_boost('dna_pattern', 1.10, ...)` / `add_boost('btc_align', ...)` → abaixo do cap

Total combinado capped em 1.80 (era ilimitado cumulativo).

## Score paths (onde decisão acontece)

- pump_trader: `score_pump()` retorna `opportunity_score` usado pelo `predictive_pump_trader.py` para:
  - threshold de alerta Telegram
  - passado ao `outcome_tracker.register_signal`
- crash_trader: análogo com `score_crash()`

Shadow mode (Commit 7): computa V2 em paralelo, outcome armazena `score_v1`, `score_v2`, `score_v2_audit`. Decisão segue V1 até `SCORING_V2_LIVE=True`.

## Próximos passos

- Commit 5: adicionar `score_pump_v2()` e `score_crash_v2()` ao lado das funções existentes
- Commit 6: regression test contra `logs/outcomes.clean.jsonl`
- Commit 7: wire shadow + config flag
