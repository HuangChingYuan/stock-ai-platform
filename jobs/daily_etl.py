"""每日盤後排程（GitHub Actions 執行）：擷取 → 指標 → LLM 報告 → Telegram 推播。

python -m jobs.daily_etl                   # 完整流程
python -m jobs.daily_etl --stocks 2330     # 指定股票
python -m jobs.daily_etl --no-llm --no-push
"""
from __future__ import annotations

from sqlalchemy import func, select

import argparse
import logging
import time
from datetime import timedelta

from core import data_sources as ds
from core import indicators as ta
from core import report as rpt
from core import repository as repo
from core.config import get_settings
from core.db import init_db, session_scope
from core.models import DailyPrice, Indicator, MonthlyRevenue, News
from core.notify import send_telegram

log = logging.getLogger("daily_etl")


def sync_stock(stock_id: str, days: int) -> None:
    today = repo.today()
    with session_scope() as s:
        existing = repo.prices_df(s, stock_id, limit=1)
    # 已有資料只補最近 10 天；第一次抓 days 天，讓 MA60 等長週期指標有足夠樣本
    start = today - timedelta(days=10 if not existing.empty else days)

    prices = ds.fetch_prices(stock_id, start)
    time.sleep(1)
    revenue = ds.fetch_month_revenue(stock_id, today - timedelta(days=800 if existing.empty else 100))
    time.sleep(1)
    news = ds.fetch_news(stock_id, today - timedelta(days=3))
    time.sleep(1)

    with session_scope() as s:
        repo.upsert(s, DailyPrice, [{"stock_id": stock_id, **r} for r in prices.to_dict("records")],
                    keys=["stock_id", "date"])
        repo.upsert(s, MonthlyRevenue, [{"stock_id": stock_id, **r} for r in revenue.to_dict("records")],
                    keys=["stock_id", "year", "month"])
        repo.upsert(s, News, [{"stock_id": stock_id, **r} for r in news.head(20).to_dict("records")],
                    keys=["stock_id", "link"])
        full = repo.prices_df(s, stock_id, limit=300)
        if not full.empty:
            ind = ta.compute(full)
            done = s.scalar(select(func.count()).select_from(Indicator)
                            .where(Indicator.stock_id == stock_id))
            if done >= len(ind) - 20:  # 已回補過：只寫最近 20 日
                ind = ind.tail(20)
            repo.upsert(s, Indicator, [{"stock_id": stock_id, **r} for r in ind.to_dict("records")],
                        keys=["stock_id", "date"])
    log.info("%s：股價 %d、營收 %d、新聞 %d 筆", stock_id, len(prices), len(revenue), len(news))


def push(stock_id: str, report: dict) -> None:
    settings = get_settings()
    with session_scope() as s:
        chats = set(repo.watchers_of(s, stock_id)) | set(settings.telegram_default_chat_ids)
        sig = ta.signals(repo.indicators_df(s, stock_id, 5), repo.prices_df(s, stock_id, 5))
        snap = repo.snapshot(s, stock_id)
    if not chats:
        return
    chg = f"（{snap['change_pct']:+.2f}%）" if snap["change_pct"] is not None else ""
    text = (f"【盤後】{stock_id} {snap['name']} 收 {snap['close']}{chg}\n"
            f"訊號：{'；'.join(sig) or '無'}\n"
            f"AI：{report['action']}（信心 {report['confidence']}）{report['summary']}\n"
            f"僅供學習研究，非投資建議")
    for chat in chats:
        send_telegram(chat, text)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stocks", help="逗號分隔，預設為 WATCHLIST ＋ Telegram 自選股")
    p.add_argument("--days", type=int, default=400)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-push", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    with session_scope() as s:
        raw = args.stocks.split(",") if args.stocks else (
            get_settings().watchlist + repo.watched_stock_ids(s))
    stocks = list(dict.fromkeys(x.strip().upper() for x in raw if x.strip()))
    if not stocks:
        raise SystemExit("股票清單是空的：請設定 WATCHLIST 或用 --stocks 指定")

    failed = []
    for sid in stocks:
        sid = sid.strip().upper()
        try:
            sync_stock(sid, args.days)
            with session_scope() as s:
                report = rpt.generate(s, sid, use_llm=not args.no_llm)
            log.info("%s 報告：%s（%s）", sid, report["action"], report["provider"])
            if not args.no_push:
                push(sid, report)
        except Exception:  # 單檔失敗不影響其他股票
            log.exception("%s 處理失敗", sid)
            failed.append(sid)
    if failed:
        raise SystemExit(f"失敗：{', '.join(failed)}")


if __name__ == "__main__":
    main()
