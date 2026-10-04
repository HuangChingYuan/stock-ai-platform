"""資料存取：upsert 與常用查詢。PostgreSQL 與 SQLite 共用同一套程式。"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from core.models import DailyPrice, Indicator, MonthlyRevenue, News, Report, Stock, Watch


# 上市櫃代號：4 碼個股、5–6 碼 ETF，可帶一個英文字尾（例：2881A、00631L、00679B）
STOCK_ID_RE = re.compile(r"\d{4,6}[A-Z]?")


def valid_stock_id(stock_id: str) -> bool:
    return bool(STOCK_ID_RE.fullmatch(stock_id or ""))


def _clean(v: Any) -> Any:
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):  # numpy 純量
        v = v.item()
        if isinstance(v, float) and math.isnan(v):
            return None
    return v


def upsert(session: Session, model, rows: Iterable[dict], keys: list[str], chunk: int = 300) -> int:
    rows = [{k: _clean(v) for k, v in r.items()} for r in rows]
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f"不支援的資料庫：{dialect}")
    for i in range(0, len(rows), chunk):
        part = rows[i : i + chunk]
        stmt = insert(model).values(part)
        update_cols = {c: stmt.excluded[c] for c in part[0] if c not in keys}
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=keys, set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=keys)
        session.execute(stmt)
    return len(rows)


# ---------- 查詢 ----------

def prices_df(session: Session, stock_id: str, limit: int = 250) -> pd.DataFrame:
    stmt = (
        select(DailyPrice).where(DailyPrice.stock_id == stock_id)
        .order_by(DailyPrice.date.desc()).limit(limit)
    )
    rows = session.scalars(stmt).all()
    df = pd.DataFrame(
        [{"date": r.date, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for r in rows]
    )
    return df.sort_values("date").reset_index(drop=True) if not df.empty else df


def indicators_df(session: Session, stock_id: str, limit: int = 250) -> pd.DataFrame:
    stmt = (
        select(Indicator).where(Indicator.stock_id == stock_id)
        .order_by(Indicator.date.desc()).limit(limit)
    )
    cols = [c.name for c in Indicator.__table__.columns if c.name != "stock_id"]
    rows = session.scalars(stmt).all()
    df = pd.DataFrame([{c: getattr(r, c) for c in cols} for r in rows])
    return df.sort_values("date").reset_index(drop=True) if not df.empty else df


def revenue_df(session: Session, stock_id: str, limit: int = 36) -> pd.DataFrame:
    stmt = (
        select(MonthlyRevenue).where(MonthlyRevenue.stock_id == stock_id)
        .order_by(MonthlyRevenue.year.desc(), MonthlyRevenue.month.desc()).limit(limit)
    )
    rows = session.scalars(stmt).all()
    df = pd.DataFrame([{"year": r.year, "month": r.month, "revenue": r.revenue} for r in rows])
    if df.empty:
        return df
    df = df.sort_values(["year", "month"]).reset_index(drop=True)
    prev = df.set_index(["year", "month"])["revenue"]
    df["yoy"] = [
        (r.revenue / prev.get((r.year - 1, r.month)) - 1) * 100
        if prev.get((r.year - 1, r.month)) else None
        for r in df.itertuples()
    ]
    return df


def latest_news(session: Session, stock_id: str, limit: int = 8) -> list[News]:
    stmt = select(News).where(News.stock_id == stock_id).order_by(News.date.desc().nulls_last()).limit(limit)
    return list(session.scalars(stmt).all())


def latest_report(session: Session, stock_id: str) -> Report | None:
    stmt = select(Report).where(Report.stock_id == stock_id).order_by(Report.date.desc()).limit(1)
    return session.scalars(stmt).first()


def stock_name(session: Session, stock_id: str) -> str:
    s = session.get(Stock, stock_id)
    return s.name if s and s.name else stock_id


def stock_names(session: Session, stock_ids: list[str]) -> dict[str, str]:
    rows = session.execute(select(Stock.stock_id, Stock.name).where(Stock.stock_id.in_(stock_ids))).all() if stock_ids else []
    names = {sid: name for sid, name in rows if name}
    return {sid: names.get(sid, sid) for sid in stock_ids}


def latest_rows(session: Session, model, stock_ids: list[str], n: int) -> dict[str, list]:
    """一次查出多檔股票各自最近 n 筆（依日期由舊到新），取代逐檔查詢。"""
    if not stock_ids:
        return {}
    rn = func.row_number().over(partition_by=model.stock_id, order_by=model.date.desc()).label("rn")
    sub = select(model, rn).where(model.stock_id.in_(stock_ids)).subquery()
    alias = aliased(model, sub)
    stmt = select(alias).where(sub.c.rn <= n).order_by(sub.c.stock_id, sub.c.date)
    out: dict[str, list] = {sid: [] for sid in stock_ids}
    for r in session.scalars(stmt):
        out[r.stock_id].append(r)
    return out


def rows_df(model, rows: list) -> pd.DataFrame:
    """把 latest_rows 的結果轉成與 prices_df / indicators_df 相同欄位的 DataFrame。"""
    cols = [c.name for c in model.__table__.columns if c.name != "stock_id"]
    return pd.DataFrame([{c: getattr(r, c) for c in cols} for r in rows], columns=cols)


def watched_stock_ids(session: Session) -> list[str]:
    return sorted(set(session.scalars(select(Watch.stock_id)).all()))


def watch_count(session: Session, chat_id: str) -> int:
    return session.scalar(select(func.count()).select_from(Watch).where(Watch.chat_id == chat_id)) or 0


def watchers_map(session: Session, stock_ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {sid: [] for sid in stock_ids}
    if stock_ids:
        for sid, chat in session.execute(select(Watch.stock_id, Watch.chat_id).where(Watch.stock_id.in_(stock_ids))):
            out[sid].append(chat)
    return out


def snapshots(session: Session, stock_ids: list[str]) -> list[dict]:
    """PWA 自選股列、Telegram /price 共用的最新報價摘要；多檔共用 2 次查詢。"""
    prices = latest_rows(session, DailyPrice, stock_ids, 2)
    names = stock_names(session, stock_ids)
    return [_snapshot(sid, names[sid], rows_df(DailyPrice, prices[sid])) for sid in stock_ids]


def snapshot(session: Session, stock_id: str) -> dict:
    return snapshots(session, [stock_id])[0]


def _snapshot(stock_id: str, name: str, df: pd.DataFrame) -> dict:
    out = {"stock_id": stock_id, "name": name, "date": None,
           "close": None, "change": None, "change_pct": None, "volume": None}
    if df.empty:
        return out
    last = df.iloc[-1]
    out.update(date=str(last["date"]), close=_clean(last["close"]), volume=_clean(last["volume"]))
    if len(df) > 1 and df.iloc[-2]["close"]:
        prev = float(df.iloc[-2]["close"])
        out["change"] = round(float(last["close"]) - prev, 2)
        out["change_pct"] = round((float(last["close"]) / prev - 1) * 100, 2)
    return out


def today() -> date:
    # 台灣時間（UTC+8）的今天；GitHub Actions 與 Render 主機都是 UTC。
    return (pd.Timestamp.utcnow() + pd.Timedelta(hours=8)).date()
