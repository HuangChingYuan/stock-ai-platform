"""讓 Streamlit Community Cloud 的 app 保持清醒（GitHub Actions 定期執行）。

Community Cloud 約 12 小時沒人瀏覽就會休眠，休眠頁要在瀏覽器裡按「Yes, get this app back up!」才會重新啟動；
單純用 curl 打網址不算瀏覽。這支用無頭瀏覽器開啟 app：休眠中就按喚醒按鈕並等它啟動，醒著就停留一下當作一次瀏覽。

STREAMLIT_URL=https://stock-ai-overview.streamlit.app python -m jobs.wake_streamlit
需要：pip install playwright && python -m playwright install --with-deps chromium
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.request

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

log = logging.getLogger("wake_streamlit")

DEFAULT_URL = "https://stock-ai-overview.streamlit.app"
WAKE_BUTTON = re.compile(r"get this app back up", re.I)
SLEEPING_STATUS = 12  # /api/v2/app/status 在休眠頁顯示時回傳的代碼（非公開文件，只用於記錄）


def app_status(base: str) -> int | None:
    """讀取 Community Cloud 的狀態代碼，只用來記錄；讀不到不影響流程。"""
    try:
        with urllib.request.urlopen(f"{base}/api/v2/app/status", timeout=20) as r:
            return json.load(r).get("status")
    except Exception as e:  # noqa: BLE001
        log.warning("讀取狀態失敗：%s", e)
        return None


def wake(base: str, screenshot: str = "streamlit-wake.png") -> bool:
    log.info("喚醒前狀態：%s", app_status(base))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(f"{base}/", wait_until="domcontentloaded", timeout=90_000)
            button = page.get_by_role("button", name=WAKE_BUTTON)
            try:
                button.wait_for(state="visible", timeout=20_000)
            except PlaywrightTimeout:
                log.info("沒有出現喚醒按鈕，app 醒著；停留 30 秒當作一次瀏覽")
                page.wait_for_timeout(30_000)
                return True

            log.info("app 休眠中，按下喚醒按鈕")
            button.click()
            try:
                button.wait_for(state="detached", timeout=300_000)
            except PlaywrightTimeout:
                log.error("按下喚醒後 5 分鐘仍停在休眠頁")
                page.screenshot(path=screenshot, full_page=True)
                return False
            page.wait_for_timeout(60_000)  # 等 app 跑完啟動，讓第一次瀏覽完整載入
            status = app_status(base)
            log.info("喚醒後狀態：%s", status)
            if status == SLEEPING_STATUS:
                log.warning("狀態代碼仍是休眠中，請到 Actions 下載截圖確認")
                page.screenshot(path=screenshot, full_page=True)
            return True
        except Exception:
            page.screenshot(path=screenshot, full_page=True)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url = (os.environ.get("STREAMLIT_URL") or DEFAULT_URL).rstrip("/")
    sys.exit(0 if wake(url) else 1)
