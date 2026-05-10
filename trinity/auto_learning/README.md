# Trinity Auto-Learning System v1.0

Sistema autonomo de aprendizado para operacao **NAO-SUPERVISIONADA** (1-3 meses sem operador).

## Filosofia

> Mudancas automaticas sao SEMPRE pequenas, lentas, reversiveis e auditadas.

## Estrutura

```
auto_learning/
  safety.py             ← kill switches, validacoes, audit log, snapshots
  metrics.py            ← calculos sobre outcomes (WR by source/symbol/etc)
  state.py              ← gerenciamento do current_config.json
  ml_auto_apply.py      ← (PROMPT 2) aplicacao de pesos ML
  threshold_tuner.py    ← (PROMPT 2) ajuste ALERT_THRESHOLD
  symbol_manager.py     ← (PROMPT 2) blacklist/whitelist auto
  regime_tuner.py       ← (PROMPT 2) regime gate auto-tune
  health_monitor.py     ← (PROMPT 3) deteccao de degradacao
  performance_guard.py  ← (PROMPT 3) kill switch geral
  weekly_report.py      ← (PROMPT 3) Telegram report semanal
  orchestrator.py       ← (PROMPT 4) roda tudo diariamente
```

## Kill Switches (env vars no Render)

```
AUTO_LEARNING_ENABLED=false   # MASTER (default: false — seguranca)
AUTO_ML_APPLY=false
AUTO_THRESHOLD_TUNE=false
AUTO_SYMBOL_MANAGE=false
AUTO_REGIME_TUNE=false
AUTO_HEALTH_MONITOR=false
AUTO_PERFORMANCE_GUARD=false
AUTO_WEEKLY_REPORT=false
```

**Pra ativar:** mudar pra `true` UM POR VEZ no Render Environment, validar 24-48h.

## Estado em /data/auto_learning/

```
/data/auto_learning/
  current_config.json       ← config ativo (lido pelo Trinity)
  changes.jsonl             ← audit log de toda mudanca
  kill_switch_log.jsonl     ← registros de kills
  health.json               ← status de cada modulo
  snapshots/                ← backup de configs antes de mudancas
    20260418-153000_threshold_change.json
    ...
  weekly_reports/           ← reports ja enviados
    2026-W17.json
```

## Caps de Seguranca (em safety.py)

| Cap | Valor |
|-----|-------|
| THRESHOLD_MIN | 25 |
| THRESHOLD_MAX | 90 |
| THRESHOLD_MAX_CHANGE_PER_RUN | 2 |
| THRESHOLD_MAX_CHANGE_PER_WEEK | 5 |
| BLACKLIST_MAX_SIZE | 100 |
| WHITELIST_MAX_SIZE | 50 |
| SYMBOL_LIST_MAX_CHANGE_PER_RUN | 5 |
| SYMBOL_LIST_MAX_CHANGE_PER_WEEK | 15 |
| ML_APPLY_MIN_SAMPLES | 1000 |
| ML_APPLY_MIN_SHARPE | 1.5 |
| EMERGENCY_WR_FLOOR | 0.30 |
| EMERGENCY_DAYS_BELOW | 3 |

## Rollback

### Desativar tudo (sem deploy):

```
Render Environment → AUTO_LEARNING_ENABLED=false → Save
```

### Voltar config pra snapshot anterior:

```bash
ls /data/auto_learning/snapshots/
# Escolher snapshot
cp /data/auto_learning/snapshots/<TIMESTAMP>_*.json /data/auto_learning/current_config.json
```

### Reverter codigo:

```bash
git checkout backup-pre-auto-learning-foundations
git push --force origin main
```

## Logs

Procurar prefixos:

```
[SAFETY]      ← safety layer
[METRICS]     ← metricas
[STATE]       ← gerenciamento de config
[ML_APPLY]    ← modulo ML auto-apply
[THRESHOLD]   ← threshold tuner
[SYMBOL]      ← symbol manager
[REGIME]      ← regime tuner
[HEALTH]      ← health monitor
[GUARD]       ← performance guard
[REPORT]      ← weekly report
[ORCHESTRA]   ← orchestrator
```
