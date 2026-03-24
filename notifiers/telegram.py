import logging

import requests

from config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_message(chat_id: int, text: str) -> bool:
    try:
        resp = requests.post(
            f"{_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Failed to send Telegram message to %s: %s", chat_id, exc)
        return False


def broadcast(chat_ids: list[int], text: str) -> None:
    for chat_id in chat_ids:
        send_message(chat_id, text)
