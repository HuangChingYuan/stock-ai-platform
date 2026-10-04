"""台股資料來源：FinMind v4 API（主要資料源）。

FinMind 免費使用即可呼叫（每小時 300 次），註冊後帶 token 提高到每小時 600 次；每週日凌晨為維護時段。
超過上限回 HTTP 402，且該 IP 會被封鎖約一小時：此時重試只會延長封鎖，必須整批停止。
資料集名稱與欄位請以 FinMind 官方文件為準：https://finmind.github.io/
"""
from __future__ import annotations

import time
from datetime import date

import pandas as pd
import requests

from core.config import get_settings
from core.repository import valid_stock_id

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


class DataSourceError(RuntimeError):
    pass


class QuotaExceededError(DataSourceError):
    """FinMind 使用量達上限（402）：不可重試，呼叫端應停止後續擷取。"""


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
            if resp.status_code == 402:
                raise QuotaExceededError(f"{dataset} {data_id}: FinMind 使用量已達上限（HTTP 402），約一小時後解除")
            if resp.status_code in (429, 500, 502, 503, 504):
                raise DataSourceError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") == 402:
                raise QuotaExceededError(f"{dataset} {data_id}: {payload.get('msg') or 'FinMind 使用量已達上限'}")
            if payload.get("status") not in (200, None):
                raise DataSourceError(payload.get("msg", "FinMind 回傳錯誤"))
            return pd.DataFrame(payload.get("data", []))
        except QuotaExceededError:
            raise
        except (requests.RequestException, DataSourceError) as exc:
            if attempt == retries:
                raise DataSourceError(f"{dataset} {data_id}: {exc}") from exc
            time.sleep(5 * attempt)
    return pd.DataFrame()


def fetch_stock_info() -> pd.DataFrame:
    df = _finmind("TaiwanStockInfo", None, date(2000, 1, 1))
    if df.empty:
        return df
    df = df.rename(columns={"stock_name": "name", "industry_category": "industry", "type": "market"})
    if "market" not in df:
        df["market"] = None
    if "date" in df:  # 上櫃轉上市的股票兩種都有，留最新的一筆
        df = df.sort_values("date", ascending=False, kind="stable")
    # 清單裡也有大盤與類股指數（如 ElectronicProductsDistribution），只留股票與 ETF 代號
    df = df[df["stock_id"].astype(str).map(valid_stock_id)]
    df = df.assign(name=df["name"].str.slice(0, 50), industry=df["industry"].str.slice(0, 50))
    return df[["stock_id", "name", "industry", "market"]].drop_duplicates("stock_id")


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


# 法人別 → 欄位；舊資料的自營商只有一筆 Dealer，之後拆成自行買賣與避險
_INSTITUTION_COLS = {
    "Foreign_Investor": "foreign_net", "Foreign_Dealer_Self": "foreign_net",
    "Investment_Trust": "trust_net",
    "Dealer": "dealer_net", "Dealer_self": "dealer_net", "Dealer_Hedging": "dealer_net",
}


def fetch_institutional(stock_id: str, start: date) -> pd.DataFrame:
    """三大法人每日買賣超（股數），每天一列：date, foreign_net, trust_net, dealer_net。"""
    df = _finmind("TaiwanStockInstitutionalInvestorsBuySell", stock_id, start)
    if df.empty:
        return df
    df = df[df["name"].isin(_INSTITUTION_COLS)].assign(
        col=lambda d: d["name"].map(_INSTITUTION_COLS),
        net=lambda d: d["buy"].astype("int64") - d["sell"].astype("int64"))
    out = df.pivot_table(index="date", columns="col", values="net", aggfunc="sum").reset_index()
    for c in ("foreign_net", "trust_net", "dealer_net"):  # pivot 後是浮點數，轉回整數，缺值存空值
        out[c] = out[c].round().astype("Int64").astype(object).where(out[c].notna(), None) if c in out else None
    out["date"] = pd.to_datetime(out["date"]).dt.date
    return out[["date", "foreign_net", "trust_net", "dealer_net"]]


def fetch_valuation(stock_id: str, start: date) -> pd.DataFrame:
    """本益比、股價淨值比、殖利率；虧損時 FinMind 的 PER 為 0，改存空值。"""
    df = _finmind("TaiwanStockPER", stock_id, start)
    if df.empty:
        return df
    df = df.rename(columns={"PER": "per", "PBR": "pbr"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["per"] = df["per"].where(df["per"] > 0)
    return df[["date", "per", "pbr", "dividend_yield"]]
