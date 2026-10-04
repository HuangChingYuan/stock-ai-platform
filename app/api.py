"""REST API：PWA 外殼、Streamlit 以外的前端、Telegram 共用。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from core import repository as repo
from core.config import get_settings
from core.db import session_scope


def _cache(response: Response) -> None:
    # 資料一天只在盤後更新一次；短暫快取可減少 Render 喚醒後的負擔（錯誤回應不會帶這個標頭）
    response.headers["Cache-Control"] = "public, max-age=300"


router = APIRouter(prefix="/api", tags=["stocks"], dependencies=[Depends(_cache)])


def _records(df):
    return [] if df.empty else df.astype(object).where(df.notna(), None).to_dict(orient="records")


@router.get("/stocks")
def list_stocks():
    """自選股清單與最新報價（環境變數 WATCHLIST ＋ Telegram 使用者追蹤的股票）。"""
    with session_scope() as s:
        ids = list(dict.fromkeys(get_settings().watchlist + repo.watched_stock_ids(s)))
        return repo.snapshots(s, ids)


@router.get("/stocks/{stock_id}/prices")
def prices(stock_id: str, days: int = Query(120, ge=1, le=1000)):
    with session_scope() as s:
        return _records(repo.prices_df(s, stock_id, limit=days))


@router.get("/stocks/{stock_id}/indicators")
def indicators(stock_id: str, days: int = Query(120, ge=1, le=1000)):
    with session_scope() as s:
        return _records(repo.indicators_df(s, stock_id, limit=days))


@router.get("/stocks/{stock_id}/report")
def report(stock_id: str):
    with session_scope() as s:
        r = repo.latest_report(s, stock_id)
        if r is None:
            raise HTTPException(404, "尚無報告")
        return {c.name: getattr(r, c.name) for c in r.__table__.columns}
