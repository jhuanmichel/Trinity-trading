"""
Trinity Auto-Learning System v1.0

Sistema autonomo de aprendizado e ajuste para operacao NAO-SUPERVISIONADA
durante 1-3 meses.

Filosofia:
    Mudancas automaticas sao SEMPRE pequenas, lentas, reversiveis e auditadas.

Modulos:
    - safety: kill switches, validacoes, audit log
    - metrics: calculos sobre outcomes
    - state: gerenciamento do config ativo
    - ml_auto_apply: aplicacao automatica de pesos ML (PROMPT 2)
    - threshold_tuner: ajuste de ALERT_THRESHOLD (PROMPT 2)
    - symbol_manager: blacklist/whitelist automatica (PROMPT 2)
    - regime_tuner: regime gate auto-tuning (PROMPT 2)
    - health_monitor: deteccao de degradacao (PROMPT 3)
    - performance_guard: kill switch geral por WR (PROMPT 3)
    - weekly_report: relatorio Telegram automatico (PROMPT 3)
    - orchestrator: roda todos os modulos diariamente (PROMPT 4)

Kill switches (env vars no Render):
    AUTO_LEARNING_ENABLED=false   # master switch (desliga TUDO)
    AUTO_ML_APPLY=false           # modulo individual
    AUTO_THRESHOLD_TUNE=false
    AUTO_SYMBOL_MANAGE=false
    AUTO_REGIME_TUNE=false
    AUTO_HEALTH_MONITOR=false
    AUTO_PERFORMANCE_GUARD=false
    AUTO_WEEKLY_REPORT=false
"""

__version__ = "1.0.0"
