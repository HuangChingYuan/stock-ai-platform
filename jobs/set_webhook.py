"""設定 Telegram webhook 指向 Render 服務：python -m jobs.set_webhook"""
from __future__ import annotations

import httpx

from core.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    if not (s.telegram_bot_token and s.public_base_url and s.telegram_webhook_secret):
        raise SystemExit("請先設定 TELEGRAM_BOT_TOKEN、PUBLIC_BASE_URL 與 TELEGRAM_WEBHOOK_SECRET"
                         "（與 Render 上的值相同，webhook 會拒絕沒有 secret 的請求）")
    payload = {"url": f"{s.public_base_url}/telegram/webhook", "allowed_updates": ["message"],
               "secret_token": s.telegram_webhook_secret}
    r = httpx.post(f"https://api.telegram.org/bot{s.telegram_bot_token}/setWebhook", json=payload, timeout=15)
    print(r.json())
