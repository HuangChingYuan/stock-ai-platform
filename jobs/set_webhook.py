"""手動設定 Telegram webhook 與指令選單：python -m jobs.set_webhook

部署在 Render 時服務啟動會自動設定，通常不需要執行這支。本機執行時 TELEGRAM_WEBHOOK_SECRET
必須和 Render 上的值相同，否則 webhook 會拒絕 Telegram 的請求。
"""
from __future__ import annotations

from app.telegram_bot import sync_webhook

if __name__ == "__main__":
    print(sync_webhook())
