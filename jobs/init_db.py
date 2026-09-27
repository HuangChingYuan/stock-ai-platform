"""建立資料表並匯入上市櫃股票清單。首次部署執行一次：python -m jobs.init_db"""
from __future__ import annotations

import logging

from core import data_sources as ds
from core import repository as repo
from core.db import init_db, session_scope
from core.models import Stock

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    init_db()
    logging.info("資料表已建立")
    try:
        info = ds.fetch_stock_info()
        with session_scope() as s:
            n = repo.upsert(s, Stock, info.to_dict("records"), keys=["stock_id"])
        logging.info("匯入股票清單 %d 筆", n)
    except ds.DataSourceError as exc:
        logging.warning("股票清單匯入失敗（可稍後重跑）：%s", exc)
