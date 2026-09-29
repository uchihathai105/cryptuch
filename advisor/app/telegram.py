"""Telegram alerts (URS F24). Token and chat id live in config.local.json on this Mac only."""
from __future__ import annotations

import logging

import httpx

from . import config

log = logging.getLogger("telegram")


def configured() -> bool:
    c = config.local_config()
    return bool(c.get("telegram_bot_token") and c.get("telegram_chat_id"))


async def send(text: str) -> tuple[bool, str]:
    c = config.local_config()
    token, chat = c.get("telegram_bot_token"), c.get("telegram_chat_id")
    if not (token and chat):
        return False, "Telegram is not set up: add telegram_bot_token and telegram_chat_id to config.local.json"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                        "disable_web_page_preview": True})
        if r.status_code != 200:
            return False, f"Telegram replied HTTP {r.status_code}: {r.text[:150]}"
        return True, "Sent"
    except httpx.HTTPError as e:
        log.warning("Telegram send failed: %s", e)
        return False, f"Could not reach Telegram: {e}"
