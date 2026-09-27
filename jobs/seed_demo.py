"""產生示範用的模擬資料（不連網、不需金鑰），方便本機測試 UI：python -m jobs.seed_demo
注意：資料為隨機產生，僅供介面測試。"""
from __future__ import annotations


import numpy as np
import pandas as pd

from core import indicators as ta
from core import report as rpt
from core import repository as repo
from core.config import get_settings
from core.db import init_db, session_scope
from core.models import DailyPrice, Indicator, MonthlyRevenue, Stock

if __name__ == "__main__":
    init_db()
    rng = np.random.default_rng(7)
    days = pd.bdate_range(end=repo.today(), periods=260)
    for i, sid in enumerate(get_settings().watchlist):
        close = 100 * (1 + i) * np.exp(np.cumsum(rng.normal(0.0005, 0.015, len(days))))
        open_ = close * (1 + rng.normal(0, 0.005, len(days)))
        df = pd.DataFrame({
            "date": days.date, "open": open_.round(2), "close": close.round(2),
            "high": (np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.006, len(days))))).round(2),
            "low": (np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.006, len(days))))).round(2),
            "volume": rng.integers(5_000_000, 40_000_000, len(days)),
        })
        months = pd.period_range(end=pd.Period(repo.today(), "M") - 1, periods=36, freq="M")
        rev = [{"stock_id": sid, "year": m.year, "month": m.month,
                "revenue": int(2e10 * (1 + i) * (1 + 0.01 * k) * rng.uniform(0.9, 1.1))} for k, m in enumerate(months)]
        with session_scope() as s:
            repo.upsert(s, Stock, [{"stock_id": sid, "name": f"示範{sid}", "industry": "示範"}], keys=["stock_id"])
            repo.upsert(s, DailyPrice, [{"stock_id": sid, **r} for r in df.to_dict("records")], keys=["stock_id", "date"])
            repo.upsert(s, MonthlyRevenue, rev, keys=["stock_id", "year", "month"])
            ind = ta.compute(df)
            repo.upsert(s, Indicator, [{"stock_id": sid, **r} for r in ind.to_dict("records")], keys=["stock_id", "date"])
            print(sid, rpt.generate(s, sid, use_llm=False)["action"])
