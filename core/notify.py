"""Telegram 發訊（排程推播與 webhook 回覆共用）。"""
from __future__ import annotations

import logging

import httpx

from core.config import get_settings

log = logging.getLogger(__name__)


def send_telegram(chat_id: str | int, text: str) -> bool:
    token = get_settings().telegram_bot_token
    if not token:
        log.info("未設定 TELEGRAM_BOT_TOKEN，略過推播：%s", text[:40])
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
                       timeout=15)
        r.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log.warning("Telegram 發送失敗（chat %s）：%s", chat_id, exc)
        return False
