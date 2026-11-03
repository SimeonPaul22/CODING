"""Optional Telegram alerts. Failures are logged, never raised: a Telegram
outage must not stop the bot from managing its positions.

Setup: talk to @BotFather on Telegram -> /newbot -> copy the token into
TELEGRAM_BOT_TOKEN. Send your new bot any message, then open
https://api.telegram.org/bot<TOKEN>/getUpdates and copy "chat":{"id": ...}
into TELEGRAM_CHAT_ID.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)

ICONS = {"trade": "💱", "error": "⚠️", "summary": "📊", "shutdown": "🛑", "info": "ℹ️"}


class Notifier:
    def __init__(self, enabled: bool, token: Optional[str], chat_id: Optional[str], prefix: str = "") -> None:
        self.enabled = bool(enabled and token and chat_id)
        if enabled and not self.enabled:
            log.warning("Telegram alerts enabled in config but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing")
        self.token = token
        self.chat_id = chat_id
        self.prefix = prefix

    def send(self, kind: str, text: str) -> bool:
        msg = f"{ICONS.get(kind, '')} {self.prefix}{text}".strip()
        log.info("ALERT[%s] %s", kind, text)
        if not self.enabled:
            return False
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": msg[:4000], "disable_web_page_preview": True},
                timeout=10,
            )
            if r.status_code != 200:
                log.warning("Telegram send failed: HTTP %s %s", r.status_code, r.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            log.warning("Telegram send failed: %s", exc)
            return False
