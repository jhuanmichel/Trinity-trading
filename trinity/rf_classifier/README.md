# Trinity Random Forest Classifier v1.0

Substitui o score composto invertido por classificacao ML supervisionada.

## Arquitetura

```
RF separado por source:
  models/pump_radar.pkl
  models/crash_radar.pkl
  models/market_movers_scanner.pkl
  models/funding_scanner.pkl

Treinado em walk-forward com 5 folds cronologicos.
Inferencia em runtime (cache em memoria).
Fail-open em qualquer erro.
```

## Kill switches (env vars no Render)

```
RF_CLASSIFIER_ENABLED=false   # MASTER (default off)
RF_OBSERVATION_MODE=true      # log mas nao filtra (recomendado primeiro)
RF_FILTER_THRESHOLD=0.55      # prob_win minima (default 0.55)
RF_AUTO_RETRAIN=false         # retreino diario 3h UTC
```

## Endpoints

```
GET  /api/rf/status          # status geral + AUC por modelo
GET  /api/rf/observations    # log A/B (modo observation)
POST /api/rf/retrain?force=true  # trigger manual
```

## Ativacao progressiva

```
DIA 1: POST /api/rf/retrain?force=true
       -> treina modelos com outcomes existentes
       -> verificar /api/rf/status, AUC >= 0.55 com is_stable=true

DIA 2-7: RF_CLASSIFIER_ENABLED=true + RF_OBSERVATION_MODE=true
         -> log de prob_win sem filtrar
         -> coletar 7-14 dias de dados

DIA 8+: RF_AUTO_RETRAIN=true
        -> modelos atualizam diariamente

DIA 14+: RF_OBSERVATION_MODE=false
         -> liga filtro com threshold 0.55
         -> ESPERAR queda de volume ~50%

DIA 21+: ajustar threshold se necessario (RF_FILTER_THRESHOLD)
```

## Rollback

### Desligar tudo (sem deploy):

```
Render Environment -> RF_CLASSIFIER_ENABLED=false -> Save
```

### Reverter codigo:

```bash
git checkout backup-pre-rf-classifier
git push --force origin main
```

## Logs

Procurar prefixos:

```
[FEAT]    feature_extractor
[PERSIST] save/load modelo
[WF]      walk_forward
[TRAIN]   trainer (1 por source)
[INFER]   inference em runtime
[OBS]     observation log
[FILTER]  filter producao
[ORCH_RF] orchestrator (retreino)
[RF_HELPER] trader helper
[RF_LOOP] async loop em server.py
```
