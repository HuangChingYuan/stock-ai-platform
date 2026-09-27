"""台股資料來源：FinMind v4 API（主要資料源）。

FinMind 免費使用即可呼叫，登入後帶 token 可提高使用上限；每週日凌晨為維護時段。
資料集名稱與欄位請以 FinMind 官方文件為準：https://finmind.github.io/
"""
from __future__ import annotations

import time
from datetime import date

import pandas as pd
import requests

from core.config import get_settings

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


class DataSourceError(RuntimeError):
    pass


def _finmind(dataset: str, data_id: str | None, start: date, end: date | None = None, retries: int = 3) -> pd.DataFrame:
    params = {"dataset": dataset, "start_date": start.isoformat()}
    if data_id:
        params["data_id"] = data_id
    if end:
        params["end_date"] = end.isoformat()
    headers = {}
    token = get_settings().finmind_token
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(FINMIND_URL, params=params, headers=headers, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise DataSourceError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") not in (200, None):
                raise DataSourceError(payload.get("msg", "FinMind 回傳錯誤"))
            return pd.DataFrame(payload.get("data", []))
        except (requests.RequestException, DataSourceError) as exc:
            if attempt == retries:
                raise DataSourceError(f"{dataset} {data_id}: {exc}") from exc
            time.sleep(5 * attempt)
    return pd.DataFrame()


def fetch_stock_info() -> pd.DataFrame:
    df = _finmind("TaiwanStockInfo", None, date(2000, 1, 1))
    if df.empty:
        return df
    df = df.rename(columns={"stock_name": "name", "industry_category": "industry"})
    return df[["stock_id", "name", "industry"]].drop_duplicates("stock_id")


def fetch_prices(stock_id: str, start: date, end: date | None = None) -> pd.DataFrame:
    df = _finmind("TaiwanStockPrice", stock_id, start, end)
    if df.empty:
        return df
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[["date", "open", "high", "low", "close", "volume"]]


def fetch_month_revenue(stock_id: str, start: date) -> pd.DataFrame:
    df = _finmind("TaiwanStockMonthRevenue", stock_id, start)
    if df.empty:
        return df
    return df.rename(columns={"revenue_year": "year", "revenue_month": "month"})[["year", "month", "revenue"]]


def fetch_news(stock_id: str, start: date) -> pd.DataFrame:
    df = _finmind("TaiwanStockNews", stock_id, start)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df[["date", "title", "source", "link"]].dropna(subset=["link"]).drop_duplicates("link")
