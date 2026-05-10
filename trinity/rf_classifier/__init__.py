"""
Trinity Random Forest Classifier v1.0

Classificador supervisionado treinado nos outcomes historicos do Trinity,
separado por source. Substitui a soma ponderada de scores que estava invertida
(overextension boost causava score alto = WR baixo).

Por que separar por source:
    pump_radar, crash_radar, market_movers_scanner, funding_scanner tem
    dinamicas e features diferentes. Um RF unico mascararia esses padroes.

Por que NAO esta em trinity/ml/:
    Legacy ml/ tem ~6k linhas de ML caseiro (random search, MLP em numpy)
    que nunca rodaram em producao. Construir aqui isolado evita divida tecnica.

Kill switches (env vars no Render):
    RF_CLASSIFIER_ENABLED=false   # master switch (default OFF)
    RF_OBSERVATION_MODE=true      # log mas nao filtra (recomendado primeiro)
    RF_FILTER_THRESHOLD=0.55      # prob_win minima pra deixar passar
    RF_AUTO_RETRAIN=false         # retreino diario as 03:00 UTC

Estado em /data/rf/:
    models/<source>.pkl           # modelos serializados
    metadata.json                 # metricas (AUC, accuracy, samples)
    feature_columns.json          # schema features
    observations.jsonl            # log A/B (modo observacao)
    retrain_log.jsonl             # historico de retreinos
"""

__version__ = "1.0.0"

# Constantes globais
MIN_SAMPLES_PER_SOURCE = 200       # minimo pra treinar RF
RECOMMENDED_SAMPLES = 500          # ideal
MIN_BALANCE_RATIO = 0.15           # min(W,L)/max(W,L) - abaixo disso usar class_weight
DEFAULT_FILTER_THRESHOLD = 0.55    # prob_win minima
WALK_FORWARD_FOLDS = 5             # validacao cronologica
