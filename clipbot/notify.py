"""Optional notifications to Discord and/or Telegram (both free)."""
from __future__ import annotations

import json
import os
import re

import requests

from .util import log


_SECRETS: dict | None = None


def _secret(name: str) -> str:
    """An env var, or the same-named GitHub secret passed in bulk as SECRETS_JSON
    (so a new brand's webhook works without editing the workflows)."""
    global _SECRETS
    if os.environ.get(name):
        return os.environ[name]
    if _SECRETS is None:
        try:
            _SECRETS = json.loads(os.environ.get("SECRETS_JSON") or "{}")
        except ValueError:
            _SECRETS = {}
    return str(_SECRETS.get(name) or "")


def brand_hook(brand: str | None) -> str:
    """A brand's own Discord server: secret DISCORD_WEBHOOK_<BRAND> (e.g. DISCORD_WEBHOOK_SURVIVAL)."""
    if not brand:
        return ""
    return _secret("DISCORD_WEBHOOK_" + re.sub(r"[^A-Za-z0-9]", "_", brand).upper())


def notify(settings: dict, text: str, channel: str | None = None, brand: str | None = None):
    """Where it goes: the brand's own server (DISCORD_WEBHOOK_<BRAND>) if set, else the platform's
    (DISCORD_WEBHOOK_TIKTOK / _YOUTUBE) for those to-dos, else the main DISCORD_WEBHOOK."""
    if not text:
        return False
    n = settings.get("notify", {})
    sent = False
    hook = brand_hook(brand) or (_secret(f"DISCORD_WEBHOOK_{channel.upper()}") if channel else "")
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
