"""每日盤後排程（GitHub Actions 執行）：擷取 → 指標 → LLM 報告 → Telegram 推播。

python -m jobs.daily_etl                   # 完整流程
python -m jobs.daily_etl --stocks 2330     # 指定股票
python -m jobs.daily_etl --no-llm --no-push
python -m jobs.daily_etl --force           # 休市日或今天已產生過報告也照跑
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select

from core import data_sources as ds
from core import indicators as ta
from core import report as rpt
from core import repository as repo
from core.config import get_settings
from core.db import init_db, session_scope
from core.models import DailyPrice, Indicator, Institutional, MonthlyRevenue, News, Report, Stock, Valuation
from core.notify import send_telegram

log = logging.getLogger("daily_etl")


def refresh_stock_info() -> int:
    """更新上市櫃股票名稱與產業（新上市、改名）。失敗只記錄警告，不影響每日流程。"""
    try:
        info = ds.fetch_stock_info()
        with session_scope() as s:
            n = repo.upsert(s, Stock, info.to_dict("records"), keys=["stock_id"])
    except Exception as exc:  # 資料源或寫入失敗都不能讓整個排程中斷
        log.warning("股票清單更新失敗（下次再試）：%s", str(exc)[:300])
        return 0
    log.info("股票清單已更新 %d 筆", n)
    return n


def sync_stock(stock_id: str, days: int) -> date | None:
    """回傳資料庫中該股最新的股價日期。"""
    today = repo.trading_day()
    with session_scope() as s:
        existing = repo.prices_df(s, stock_id, limit=1)
        has_flows = not repo.institutional_df(s, stock_id, limit=1).empty
        has_valuation = not repo.valuation_df(s, stock_id, limit=1).empty
    # 已有資料只補最近 10 天；第一次抓 days 天，讓 MA60 等長週期指標有足夠樣本
    start = today - timedelta(days=10 if not existing.empty else days)

    prices = ds.fetch_prices(stock_id, start)
    time.sleep(1)
    revenue = ds.fetch_month_revenue(stock_id, today - timedelta(days=800 if existing.empty else 100))
    time.sleep(1)
    news = ds.fetch_news(stock_id, today - timedelta(days=3))
    time.sleep(1)
    # 法人與本益比依各自資料表判斷是否回補（既有股票升級後也會補）；本益比回補一年，報告才有區間可比較
    flows = _optional(ds.fetch_institutional, stock_id, today - timedelta(days=10 if has_flows else 30))
    valuation = _optional(ds.fetch_valuation, stock_id, today - timedelta(days=10 if has_valuation else 400))

    with session_scope() as s:
        repo.upsert(s, DailyPrice, [{"stock_id": stock_id, **r} for r in prices.to_dict("records")],
                    keys=["stock_id", "date"])
        repo.upsert(s, MonthlyRevenue, [{"stock_id": stock_id, **r} for r in revenue.to_dict("records")],
                    keys=["stock_id", "year", "month"])
        repo.upsert(s, News, [{"stock_id": stock_id, **r} for r in news.head(20).to_dict("records")],
                    keys=["stock_id", "link"])
        repo.upsert(s, Institutional, [{"stock_id": stock_id, **r} for r in flows.to_dict("records")],
                    keys=["stock_id", "date"])
        repo.upsert(s, Valuation, [{"stock_id": stock_id, **r} for r in valuation.to_dict("records")],
                    keys=["stock_id", "date"])
        full = repo.prices_df(s, stock_id, limit=300)
        if not full.empty:
            ind = ta.compute(full)
            done = s.scalar(select(func.count()).select_from(Indicator)
                            .where(Indicator.stock_id == stock_id))
            if done >= len(ind) - 20:  # 已回補過：只寫最近 20 日
                ind = ind.tail(20)
            repo.upsert(s, Indicator, [{"stock_id": stock_id, **r} for r in ind.to_dict("records")],
                        keys=["stock_id", "date"])
    log.info("%s：股價 %d、營收 %d、新聞 %d、法人 %d、本益比 %d 筆",
             stock_id, len(prices), len(revenue), len(news), len(flows), len(valuation))
    return full.iloc[-1]["date"] if not full.empty else None


def _optional(fetch, stock_id: str, start: date):
    """法人、本益比只是報告的補充資料：抓不到就略過，不讓整檔股票失敗。"""
    try:
        df = fetch(stock_id, start)
    except Exception as exc:
        log.warning("%s %s 失敗（略過）：%s", stock_id, fetch.__name__, str(exc)[:200])
        df = pd.DataFrame()
    time.sleep(1)
    return df


def push_digest(reports: dict[str, dict]) -> None:
    """每個對話只收一則盤後彙整（太長才分段），取代每檔股票各發一則。"""
    if not reports:
        return
    settings = get_settings()
    ids = list(reports)
    with session_scope() as s:
        watchers = repo.watchers_map(s, ids)
        snaps = {x["stock_id"]: x for x in repo.snapshots(s, ids)}
        inds = repo.latest_rows(s, Indicator, ids, 2)
        prices = repo.latest_rows(s, DailyPrice, ids, 2)

    blocks = {}
    for sid, report in reports.items():
        snap = snaps[sid]
        sig = ta.signals(repo.rows_df(Indicator, inds[sid]), repo.rows_df(DailyPrice, prices[sid]))
        chg = f"（{snap['change_pct']:+.2f}%）" if snap["change_pct"] is not None else ""
        blocks[sid] = (f"{sid} {snap['name']} 收 {snap['close']}{chg}\n"
                       f"訊號：{'；'.join(sig) or '無'}\n"
                       f"AI：{report['action']}（信心 {report['confidence']}）{report['summary']}")

    targets: dict[str, list[str]] = {}
    for sid in ids:
        for chat in [*watchers[sid], *settings.telegram_default_chat_ids]:
            if chat == repo.PWA_CHAT_ID:  # PWA 加的自選股只更新資料，沒有 Telegram 對話可推播
                continue
            if sid not in targets.setdefault(chat, []):
                targets[chat].append(sid)
    header = f"【盤後】{max(r['date'] for r in reports.values())}"
    for chat, sids in targets.items():
        for text in chunk_messages(header, [blocks[x] for x in sids], "僅供學習研究，非投資建議"):
            send_telegram(chat, text)


def chunk_messages(header: str, blocks: list[str], footer: str, limit: int = 4000) -> list[str]:
    """Telegram 單則上限 4096 字，超過就分成多則，每則都帶標題與免責聲明。"""
    out, cur = [], [header]
    for b in blocks:
        if len(cur) > 1 and len("\n\n".join([*cur, b, footer])) > limit:
            out.append("\n\n".join([*cur, footer]))
            cur = [header]
        cur.append(b)
    out.append("\n\n".join([*cur, footer]))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stocks", help="逗號分隔，預設為 WATCHLIST ＋ Telegram 自選股")
    p.add_argument("--days", type=int, default=400)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--force", action="store_true", help="休市日或已產生過報告也照樣產生並推播")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    with session_scope() as s:
        raw = args.stocks.split(",") if args.stocks else (
            get_settings().watchlist + repo.watched_stock_ids(s))
    stocks = list(dict.fromkeys(x.strip().upper() for x in raw if x.strip()))
    invalid = [x for x in stocks if not repo.valid_stock_id(x)]
    if invalid:
        log.warning("略過無效代號：%s", ", ".join(invalid))
    stocks = [x for x in stocks if repo.valid_stock_id(x)]
    limit = get_settings().max_stocks
    if len(stocks) > limit:  # WATCHLIST 排在前面，超出的是 Telegram 使用者後加的
        log.warning("股票數 %d 超過上限 MAX_STOCKS=%d，略過：%s", len(stocks), limit, ", ".join(stocks[limit:]))
        stocks = stocks[:limit]
    if not stocks:
        raise SystemExit("股票清單是空的：請設定 WATCHLIST 或用 --stocks 指定")

    today = repo.trading_day()
    with session_scope() as s:
        names = repo.stock_names(s, stocks)
        no_market = repo.markets_missing(s, stocks)
    missing = [sid for sid, name in names.items() if name == sid]
    if today.weekday() == 0 or missing or no_market:  # 每週一更新一次；有股票還沒有名稱或上市櫃別就立刻更新
        refresh_stock_info()

    failed, reports = [], {}
    for sid in stocks:
        try:
            latest = sync_stock(sid, args.days)
            if not args.force:
                if latest != today:  # 休市日（含颱風假）或 FinMind 尚未更新：不重複產生報告
                    log.info("%s 最新股價日期 %s 不是今天，略過報告與推播", sid, latest)
                    continue
                with session_scope() as s:
                    done = s.get(Report, (sid, latest)) is not None
                if done:  # 同一天重跑：避免重複呼叫 LLM 與重複推播
                    log.info("%s 今天已產生過報告，略過（要重跑請加 --force）", sid)
                    continue
            reports[sid] = rpt.generate(sid, use_llm=not args.no_llm)
            log.info("%s 報告：%s（%s）", sid, reports[sid]["action"], reports[sid]["provider"])
        except Exception:  # 單檔失敗不影響其他股票
            log.exception("%s 處理失敗", sid)
            failed.append(sid)
    if not args.no_push:
        try:
            push_digest(reports)
        except Exception:
            log.exception("推播失敗")
            failed.append("推播")
    if failed:
        raise SystemExit(f"失敗：{', '.join(failed)}")


if __name__ == "__main__":
    main()
