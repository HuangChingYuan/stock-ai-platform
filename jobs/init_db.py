"""建立資料表並匯入上市櫃股票清單。首次部署執行一次：python -m jobs.init_db
（每日排程也會自動建立資料表、每週一更新股票清單，不執行這支也可以。）"""
from __future__ import annotations

import logging

from core.db import init_db
from jobs.daily_etl import refresh_stock_info

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    init_db()
    logging.info("資料表已建立")
    refresh_stock_info()
