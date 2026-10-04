"""REST API：PWA 外殼、Streamlit 以外的前端、Telegram 共用。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select

from core import data_sources as ds
from core import repository as repo
from core.config import get_settings
from core.db import session_scope
from core.models import Watch

log = logging.getLogger(__name__)


def _cache(response: Response) -> None:
    # 資料一天只在盤後更新一次；短暫快取可減少 Render 喚醒後的負擔（錯誤回應不會帶這個標頭）
    response.headers["Cache-Control"] = "public, max-age=300"


router = APIRouter(prefix="/api", tags=["stocks"], dependencies=[Depends(_cache)])


def _records(df):
    return [] if df.empty else df.astype(object).where(df.notna(), None).to_dict(orient="records")


@router.get("/stocks")
def list_stocks():
    """自選股清單與最新報價（環境變數 WATCHLIST ＋ Telegram 與 PWA 加入的股票）。removable：可從 PWA 移除。"""
    with session_scope() as s:
        ids = list(dict.fromkeys(get_settings().watchlist + repo.watched_stock_ids(s)))
        pwa = set(_pwa_ids(s))
        return [{**snap, "removable": snap["stock_id"] in pwa} for snap in repo.snapshots(s, ids)]


@router.get("/industries")
def list_industries():
    """類股清單（PWA「類股」選單），每筆含市場別與檔數。market：twse 上市、tpex 上櫃、emerging 興櫃。"""
    with session_scope() as s:
        return repo.industries(s)


@router.get("/industries/stocks")
def industry_stocks(industry: str = Query(..., min_length=1, max_length=50),
                    market: str | None = Query(None, max_length=10)):
    """某類股的股票代號與名稱，可用 market 限定上市或上櫃。類股名稱用查詢參數，避免名稱裡的斜線被當成路徑。"""
    with session_scope() as s:
        return repo.stocks_in_industry(s, industry, market)


def _pwa_ids(session) -> list[str]:
    return list(session.scalars(select(Watch.stock_id).where(Watch.chat_id == repo.PWA_CHAT_ID)))


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


# 會寫入資料庫的端點：不掛 _cache，回應不能被瀏覽器快取
watch_router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@watch_router.post("/{stock_id}")
def add_watch(stock_id: str):
    """PWA 按「查看」：加入自選股；資料庫還沒有這檔的股價就立刻向 FinMind 抓。"""
    sid = stock_id.strip().upper()
    if not repo.valid_stock_id(sid):
        raise HTTPException(422, "不是有效的股票代號")
    settings = get_settings()
    with session_scope() as s:
        if not repo.stock_known(s, sid):
            raise HTTPException(404, f"查無股票 {sid}")
        listed = sid in settings.watchlist or s.get(Watch, (repo.PWA_CHAT_ID, sid)) is not None
        if not listed and repo.watch_count(s, repo.PWA_CHAT_ID) >= settings.max_watch_per_chat:
            raise HTTPException(409, f"自選股最多 {settings.max_watch_per_chat} 檔，請先移除一些")
        has_data = not repo.prices_df(s, sid, limit=1).empty

    fetched = False
    if not has_data:
        from jobs.daily_etl import sync_stock  # 與每日排程共用同一套擷取與指標計算

        try:
            fetched = sync_stock(sid, 400) is not None
        except ds.QuotaExceededError as exc:
            log.warning("即時擷取 %s 失敗：%s", sid, exc)
            raise HTTPException(503, "FinMind 使用量已達上限，約一小時後再試") from exc
        except ds.DataSourceError as exc:
            log.warning("即時擷取 %s 失敗：%s", sid, exc)
            raise HTTPException(502, "資料來源暫時無法連線，請稍後再試") from exc
        if not fetched:
            raise HTTPException(404, f"FinMind 沒有 {sid} 的股價資料")

    with session_scope() as s:
        if sid not in settings.watchlist:
            repo.upsert(s, Watch, [{"chat_id": repo.PWA_CHAT_ID, "stock_id": sid}], keys=["chat_id", "stock_id"])
        return {**repo.snapshot(s, sid), "removable": sid not in settings.watchlist, "fetched": fetched}


@watch_router.delete("/{stock_id}")
def remove_watch(stock_id: str):
    """已不在清單也回成功（重按、兩個分頁同時移除）；環境變數 WATCHLIST 的股票刪不掉，明確回錯誤而不是假裝成功。"""
    sid = stock_id.strip().upper()
    if sid in get_settings().watchlist:
        raise HTTPException(409, f"{sid} 由環境變數 WATCHLIST 設定，無法從網頁移除")
    with session_scope() as s:
        removed = s.query(Watch).filter_by(chat_id=repo.PWA_CHAT_ID, stock_id=sid).delete()
    return {"ok": True, "removed": bool(removed)}
