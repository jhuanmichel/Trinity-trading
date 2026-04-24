# ML Pipeline Inventory (Fase A)

## Invocação

- **MLManager é chamado em:** `dashboard/server.py:1441-1442` (lazy-init via `_get_ml_mgr()`)
- **Trigger atual:** manual via endpoint HTTP `GET|POST /api/ml/run` (linha 1446-1458)
- **Frequência automática:** **NENHUMA.** Não há schedule periódico.
- **Auto-trigger indireto:** `ml_manager._load_persisted()` (linha 411-427) dispara pipeline APENAS no primeiro `_get_ml_mgr()` **se** `n_total == 0` no cache persistido E há outcomes no disco. Ou seja, roda 1x após primeiro redeploy, depois nunca mais.
- **Método invocado:** `MLManager.run_async()` → dispara thread daemon que chama `_run_pipeline()` (linha 163-178)

## Consumo dos JSONs (grep exclusivo fora de `trinity/ml/`)

| Arquivo | Lido por |
|---|---|
| `ml_weight_optimizer.json` | **Ninguém** fora dos próprios writers. Ver `dashboard/server.py:1599` `/api/ml/weights` — mas isso só re-expõe via MLManager.get_results(), não consome |
| `ml_feature_importance.json` | **Ninguém** (idem) |
| `ml_walk_forward.json` | **Ninguém** |
| `ml_monte_carlo.json` | **Ninguém** |

**Conclusão:** pesos otimizados são escritos mas **nunca aplicados aos scoring engines**. `weight_optimizer.py:21` explicita: `"READ-ONLY: nunca modifica scoring engines"`.

## Dataset usado pelo MLManager

- **Arquivo:** `/data/logs/outcomes_*.jsonl` (glob mensal, ex `outcomes_2026-04.jsonl`)
- Lido por:
  - `feature_importance.py:92` → `LOGS_DIR.glob("outcomes_*.jsonl")`
  - `weight_optimizer.py:80` → idem
  - `walk_forward.py` (não verificado linha, mesmo padrão)
- **Não usa `outcomes.clean.jsonl`.** Opera sobre RAW outcomes (6980 linhas em produção).
- Em prod (via `/api/debug/storage`):
  - `/data/logs/outcomes_2026-04.jsonl`: 6980 outcomes, 3.85MB
  - `/data/logs/pending_outcomes.jsonl`: 9406 pending

## Scoring engines

### Pump (`trinity/traders/predictive_pump_trader/pump_scoring_engine.py`)

- **Função de score final:** `score_pump(silent_result, squeeze_result, gravity_result, breakout_result, coin_data)` linha 73
- **Formula (linha 103):** `opportunity_score_raw = silent_score + squeeze_score + gravity_score + breakout_score` — **soma simples**, sem pesos nomeados. Pesos implícitos 1.0 cada.
- **Pesos hardcoded?** Não como array; são implícitos na soma + caps individuais `min(25.0, ...)` por componente
- **Nomes EXATOS dos detectores (linha 119-124):** `silent_acc`, `squeeze`, `gravity`, `breakout`
- **Boosts downstream** (linhas 217+): funding_extreme, overext, momentum, leverage, DNA, blue_chip_boost 1.12x — aplicados ao `opportunity_score` via multiplicação encadeada (problema mapeado na Fase 2 anterior, já mitigado via BoostManager V2 shadow)

### Crash (`trinity/traders/predictive_crash_trader/crash_scoring_engine.py`)

- **Função:** `score_crash(cascade_m4_result, liq_result, whale_result, vol_result, coin_data)` linha 75
- **Formula (linha 105):** `opportunity_score_raw = cascade_score + collapse_score + whale_score + vol_score` — soma simples
- **Pesos hardcoded?** Mesmo padrão do pump (implícitos 1.0 cada)
- **Nomes EXATOS dos detectores (linha 121-126):** `cascade`, `collapse`, `whale`, `volatility`
- **Boosts downstream**: overext_mult (até 1.70x linha 221), lev_amp (1.25x), funding_extreme (via composite_mult), DNA, blue_chip (1.12x linha 295) — mesma cadeia multiplicativa

### IMPORTANTE — compatibilidade de keys com ML output

O `weight_optimizer.py` escreve `weights` com chaves que vêm da **pipeline ML**, que lê do `outcomes_*.jsonl` e usa `layer_scores` armazenados nos outcomes. As keys dependem do que foi persistido em `register_signal`:

```python
# Em outcome_tracker.register_signal → entry["layer_scores"] = signal.get("layer_scores", {})
# Os traders passam:
#   pump:  {"silent_acc": ..., "squeeze": ..., "gravity": ..., "breakout": ...}
#   crash: {"cascade": ..., "collapse": ..., "whale": ..., "volatility": ...}
```

Então `ml_weight_optimizer.json` teria:
- `LONG.weights = {"silent_acc": w1, "squeeze": w2, "gravity": w3, "breakout": w4}` (quando recommend=True)
- `SHORT.weights = {"cascade": w1, "collapse": w2, "whale": w3, "volatility": w4}` (idem)

Keys do ML **batem com** component_scores dos engines. Não há remap necessário.

## Scheduler

- **Lib:** `schedule` (simple Python, não APScheduler). Thread-based.
- **Tipo:** BackgroundScheduler equivalente (thread dedicada via `threading.Thread(target=_run_scheduler)` em `start.py:44`)
- **Registro de jobs:** `start.py:_run_scheduler()` linha 46-∞. Padrão:
  ```python
  schedule.every(N).minutes.do(fn)
  schedule.every().day.at("00:05").do(fn)
  ```
- **Nenhum job atual para MLManager.**

## Artefatos em produção (via `/api/debug/storage` + `/api/ml/results`)

| Arquivo | Existe | Tamanho/Idade | Status |
|---|---|---|---|
| `/data/logs/outcomes_2026-04.jsonl` | Sim | 3.85MB / 6980 linhas | Ativo, sendo escrito |
| `/data/logs/pending_outcomes.jsonl` | Sim | 3.74MB / 9406 linhas | Ativo |
| `logs/outcomes.clean.jsonl` (repo) | Sim | 2.18MB / 3846 linhas | Checkpoint 22/abr |
| `dashboard/ml_results.json` (local) | Sim | **3 bytes (`{}`)** | Vazio; never populated |
| `dashboard/ml_weight_optimizer.json` | **NÃO** | — | Nunca gerado |
| `dashboard/ml_feature_importance.json` | **NÃO** | — | Nunca gerado |
| `dashboard/ml_walk_forward.json` | **NÃO** | — | Nunca gerado |
| `dashboard/ml_monte_carlo.json` | **NÃO** | — | Nunca gerado |
| `/api/ml/results` em prod | — | — | Retorna `{"status": "no_data"}` |
| `/api/ml/weights` em prod | — | — | Retorna `{"long":{"weights":null,"applied":false},"short":{...}}` |

## Shape de `ml_weight_optimizer.json` (quando gerado)

Deduzido de `weight_optimizer.py:548-559`:

```json
{
  "LONG": {
    "status": "ok",
    "recommend": true|false,
    "weights": { "silent_acc": 23.4, "squeeze": 27.1, "gravity": 25.0, "breakout": 24.5 },
    "sharpe": 0.88,
    "auc": 0.62,
    "baseline_sharpe": 0.50,
    "baseline_auc": 0.55,
    "improvement_auc": 0.07,
    "cv": {...},
    "threshold": {...}
  },
  "SHORT": { /* idem com keys cascade/collapse/whale/volatility */ }
}
```

**Shape bate com o assumido pelo WeightsLoader do prompt.** Parser não precisa adaptação.

## Diagnóstico sintético

**Loop ML está totalmente aberto hoje:**

1. **MLManager não tem schedule automático** — só roda 1x no boot (via auto-trigger do `_load_persisted`) ou sob trigger manual HTTP. Primeira run em prod provavelmente falhou (`n_total` ainda mostra no_data apesar de 6980 outcomes disponíveis — investigar). Mesmo que sucedesse, não há cadência de retreino.
2. **Pesos otimizados não são consumidos** pelos scoring engines. `weight_optimizer.py:21` afirma textualmente "READ-ONLY: nunca modifica scoring engines". Pesos ficam no JSON, orphan.
3. Portanto: novos outcomes **não alimentam** retreino automático, e retreino (quando rodar) **não afeta** score futuro. Zero auto-melhoria.

## Fix mínimo para fechar o loop

1. **Schedule MLManager a cada 6h** via `schedule.every(6).hours.do(...)` em `start.py`
2. **Criar WeightsLoader** que lê `ml_weight_optimizer.json` com cache TTL 5min, fail-safe → None
3. **Integrar WeightsLoader em pump_scoring_engine e crash_scoring_engine** no ponto onde `opportunity_score_raw` é calculado — se ML weights presentes e recommend=True, usar; senão fallback pra soma atual (equal weights)
4. **Endpoint `/api/ml/status`** expõe saúde do pipeline + cache do loader
5. **Persistir `weight_source`** ("ml" | "legacy" | "legacy_fallback") no outcome para análise retrospectiva

Nenhuma mudança quebra contratos existentes: fallback legacy é idêntico ao comportamento atual.

## Investigação extra — por que `/api/ml/results` = no_data em prod se há 6980 outcomes?

Possíveis causas (para Fase B verificar nos logs):
- Primeiro auto-trigger falhou (exception em `_run_pipeline`) e nunca retomou
- `_load_persisted` não detectou `n_total == 0` (se results.json estava semi-populado)
- Thread daemon morreu silenciosamente

Ação Fase B: ao adicionar o schedule, adicionar também log de erro visível. Se primeira run continuar falhando, investigar separadamente.
