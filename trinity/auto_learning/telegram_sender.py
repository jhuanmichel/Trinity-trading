"""
Wrapper pra enviar mensagens Telegram de forma resiliente.
Usado pelos modulos de health/report.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("auto_learning.telegram_sender")


def send_telegram_message(message: str, parse_mode: str = "HTML") -> bool:
    """
    Envia mensagem ao Telegram bot configurado.

    Args:
        message: texto da mensagem (suporta HTML basico se parse_mode=HTML)
        parse_mode: "HTML" ou "Markdown" ou None

    Retorna True se sucesso, False se falha.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.warning("[TG_SEND] TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID ausentes")
        return False

    try:
        import requests
    except ImportError:
        logger.error("[TG_SEND] requests nao disponivel")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("[TG_SEND] Mensagem enviada")
            return True
        else:
            logger.error(
                f"[TG_SEND] Falha {response.status_code}: {response.text[:200]}"
            )
            return False
    except Exception as e:
        logger.error(f"[TG_SEND] Exception: {e}")
        return False


def send_telegram_alert(level: str, title: str, body: str) -> bool:
    """
    Envia alerta formatado.

    level: "info" / "warning" / "error" / "critical"
    """
    emoji_map = {
        "info": "[INFO]",
        "warning": "[WARNING]",
        "error": "[ERROR]",
        "critical": "[CRITICAL]",
    }
    emoji = emoji_map.get(level, "[ALERT]")

    msg = f"{emoji} <b>{title}</b>\n\n{body}"
    return send_telegram_message(msg, parse_mode="HTML")
