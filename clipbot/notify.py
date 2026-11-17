"""Optional notifications to Discord and/or Telegram (both free)."""
from __future__ import annotations

import os

import requests

from .util import log


def notify(settings: dict, text: str, channel: str | None = None):
    """channel="tiktok" / "youtube" sends to DISCORD_WEBHOOK_TIKTOK / DISCORD_WEBHOOK_YOUTUBE
    when that secret is set; otherwise everything goes to the main DISCORD_WEBHOOK."""
    if not text:
        return False
    n = settings.get("notify", {})
    sent = False
    hook = os.environ.get(f"DISCORD_WEBHOOK_{channel.upper()}", "") if channel else ""
    hook = hook or os.environ.get(n.get("discord_webhook_env", "DISCORD_WEBHOOK") or "")
    if hook:
        for chunk in _chunks(text, 1900):
            try:
                requests.post(hook, json={"content": chunk}, timeout=20)
                sent = True
            except requests.RequestException as e:
                log.warning("Discord notify failed: %s", e)
    tok = os.environ.get(n.get("telegram_token_env", "TELEGRAM_BOT_TOKEN") or "")
    chat = os.environ.get(n.get("telegram_chat_env", "TELEGRAM_CHAT_ID") or "")
    if tok and chat:
        for chunk in _chunks(text, 3900):
            try:
                requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": chat, "text": chunk, "disable_web_page_preview": True}, timeout=20)
                sent = True
            except requests.RequestException as e:
                log.warning("Telegram notify failed: %s", e)
    return sent


def _chunks(text: str, n: int):
    for i in range(0, len(text), n):
        yield text[i:i + n]
