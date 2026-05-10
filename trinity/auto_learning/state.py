"""
Gerenciamento do current_config.json.

Esse e o UNICO ponto de verdade pra configuracoes que o auto-learning controla.
Os modulos do Trinity (pump_trader, crash_trader, etc) leem daqui.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from trinity.auto_learning.safety import get_paths, create_snapshot, log_change

logger = logging.getLogger("auto_learning.state")


# Configuracao inicial padrao (usada se current_config.json nao existir)
DEFAULT_CONFIG = {
    "version": 1,
    "created_at": None,
    "last_modified": None,

    # ALERT_THRESHOLD por tipo de signal
    "thresholds": {
        "pump": 80,
        "crash": 80,
        "blue_chip_pump": 75,
        "blue_chip_crash": 75,
    },

    # Sources com Telegram silenciado
    "disabled_telegram_sources": [],

    # Symbol filters
    "symbol_blacklist": [],
    "symbol_whitelist": [],

    # Direction x Regime gate rules
    # values: "ALLOW" | "BLOCK" | "RESTRICT_SCORE"
    "regime_gate": {
        "LONG_STRONG_BEAR": "BLOCK",
        "LONG_BEAR": "BLOCK",
        "LONG_NEUTRAL": "ALLOW",
        "LONG_BULL": "ALLOW",
        "LONG_STRONG_BULL": "ALLOW",
        "SHORT_STRONG_BEAR": "ALLOW",
        "SHORT_BEAR": "ALLOW",
        "SHORT_NEUTRAL": "ALLOW",
        "SHORT_BULL": "ALLOW",
        "SHORT_STRONG_BULL": "ALLOW",
    },

    # ML weights (vazio = usa pesos default)
    "ml_weights_applied": False,
    "ml_weights_applied_at": None,
    "ml_weights_data": {},

    # Master flags (usadas por outros modulos pra desabilitar coisas via state)
    "flags": {
        "telegram_enabled": True,
        "all_filters_enabled": True,
    },
}


def get_config_path() -> Path:
    """Retorna path do current_config.json."""
    return get_paths()["current_config"]


def load_config() -> dict:
    """
    Carrega config atual.
    Se nao existir, cria com defaults.
    """
    path = get_config_path()

    if not path.exists():
        logger.info("[STATE] current_config.json nao existe - criando com defaults")
        config = dict(DEFAULT_CONFIG)
        config["created_at"] = datetime.now(timezone.utc).isoformat()
        config["last_modified"] = config["created_at"]
        save_config(config, snapshot=False)  # primeiro save sem snapshot
        return config

    try:
        with open(path) as f:
            config = json.load(f)

        # Mesclar com defaults pra adicionar campos novos sem quebrar
        for key, default_val in DEFAULT_CONFIG.items():
            if key not in config:
                config[key] = default_val

        return config
    except Exception as e:
        logger.error(f"[STATE] Erro carregando config: {e} - usando defaults")
        return dict(DEFAULT_CONFIG)


def save_config(config: dict, snapshot: bool = True, reason: str = "save") -> bool:
    """
    Salva config no disco.

    Args:
        snapshot: se True, cria snapshot antes de salvar (PADRAO recomendado)
        reason: identificador pra snapshot

    Retorna True se sucesso.
    """
    path = get_config_path()

    # Snapshot do estado atual ANTES de sobrescrever
    if snapshot and path.exists():
        try:
            with open(path) as f:
                old_config = json.load(f)
            create_snapshot(reason=f"pre_save_{reason}", config_data=old_config)
        except Exception as e:
            logger.warning(f"[STATE] Falha snapshot pre-save: {e}")

    # Atualizar timestamp
    config["last_modified"] = datetime.now(timezone.utc).isoformat()

    try:
        # Atomic write: escrever em .tmp e renomear
        tmp_path = path.with_suffix(".json.tmp")
        with open(tmp_path, "w") as f:
            json.dump(config, f, indent=2, default=str)
        tmp_path.replace(path)

        logger.info(f"[STATE] Config salvo (reason={reason})")
        return True
    except Exception as e:
        logger.error(f"[STATE] Falha salvando config: {e}")
        return False


def get_threshold(signal_type: str = "pump", is_blue_chip: bool = False) -> float:
    """
    Retorna threshold ativo pra signal_type.

    Args:
        signal_type: "pump" ou "crash"
        is_blue_chip: True se simbolo e blue chip
    """
    config = load_config()
    thresholds = config.get("thresholds", {})

    if is_blue_chip:
        key = f"blue_chip_{signal_type}"
        return float(thresholds.get(key, thresholds.get(signal_type, 80)))

    return float(thresholds.get(signal_type, 80))


def is_telegram_disabled_for_source(source: str) -> bool:
    """Verifica se source esta com Telegram silenciado."""
    config = load_config()
    disabled = config.get("disabled_telegram_sources", [])
    return source in disabled


def is_symbol_blacklisted(symbol: str) -> bool:
    """Verifica se simbolo esta na blacklist."""
    if not symbol:
        return False
    config = load_config()
    blacklist = {s.upper() for s in config.get("symbol_blacklist", [])}
    return symbol.upper() in blacklist


def is_symbol_whitelisted(symbol: str) -> bool:
    """Verifica se simbolo esta na whitelist."""
    if not symbol:
        return False
    config = load_config()
    whitelist = {s.upper() for s in config.get("symbol_whitelist", [])}
    return symbol.upper() in whitelist


def get_regime_gate_rule(direction: str, regime: str) -> str:
    """
    Retorna regra do regime gate.

    Returns: "ALLOW" | "BLOCK" | "RESTRICT_SCORE"

    Default: "ALLOW" (fail-open).
    """
    config = load_config()
    gate = config.get("regime_gate", {})
    key = f"{direction.upper()}_{regime.upper()}"
    return gate.get(key, "ALLOW")


def get_config_summary() -> dict:
    """Resumo do config pra dashboards/relatorios."""
    config = load_config()
    return {
        "version": config.get("version"),
        "last_modified": config.get("last_modified"),
        "thresholds": config.get("thresholds", {}),
        "blacklist_count": len(config.get("symbol_blacklist", [])),
        "whitelist_count": len(config.get("symbol_whitelist", [])),
        "disabled_sources_count": len(config.get("disabled_telegram_sources", [])),
        "ml_weights_applied": config.get("ml_weights_applied", False),
        "ml_weights_applied_at": config.get("ml_weights_applied_at"),
    }
