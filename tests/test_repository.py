from datetime import date, datetime

from core import report as rpt
from core import repository as repo
from core.db import session_scope
from core.models import DailyPrice, MonthlyRevenue, News, Stock


def test_valid_stock_id():
    for ok in ("2330", "0050", "00631L", "00679B", "2881A", "006208"):
        assert repo.valid_stock_id(ok)
    for bad in ("", "233", "ABCD", "2330XX", "1234567", "2330\n", "../x"):
        assert not repo.valid_stock_id(bad)


def test_latest_news_puts_undated_last(db):
    with session_scope() as s:
        repo.upsert(s, News, [
            {"stock_id": "2330", "date": None, "title": "無日期", "source": "x", "link": "a"},
            {"stock_id": "2330", "date": datetime(2026, 1, 2), "title": "新", "source": "x", "link": "b"},
            {"stock_id": "2330", "date": datetime(2026, 1, 1), "title": "舊", "source": "x", "link": "c"},
        ], keys=["stock_id", "link"])
    with session_scope() as s:
        assert [n.title for n in repo.latest_news(s, "2330", limit=2)] == ["新", "舊"]


def test_report_context_has_yoy_for_all_six_months(db):
    rows, y, m = [], 2024, 1
    for _ in range(24):
        rows.append({"stock_id": "2330", "year": y, "month": m, "revenue": 1_000_000})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    with session_scope() as s:
        repo.upsert(s, MonthlyRevenue, rows, keys=["stock_id", "year", "month"])
    with session_scope() as s:
        context, _, _ = rpt.build_context(s, "2330")
    line = next(x for x in context.splitlines() if x.startswith("月營收"))
    assert line.count("%") == 6 + 1  # 6 個月的年增率，加上標題裡的「年增%」


def _price(sid, d, close):
    return {"stock_id": sid, "date": d, "open": close, "high": close, "low": close, "close": close, "volume": 100}


def test_snapshots_batch_matches_single(db):
    with session_scope() as s:
        repo.upsert(s, Stock, [{"stock_id": "2330", "name": "台積電", "industry": "半導體"}], keys=["stock_id"])
        repo.upsert(s, DailyPrice, [_price("2330", date(2026, 1, d), 100.0 + d) for d in (2, 5, 6)]
                    + [_price("2317", date(2026, 1, 6), 200.0)], keys=["stock_id", "date"])
    ids = ["2330", "2317", "9999"]
    with session_scope() as s:
        batch = repo.snapshots(s, ids)
        rows = repo.latest_rows(s, DailyPrice, ids, 2)
    assert [r.date.day for r in rows["2330"]] == [5, 6] and rows["9999"] == []
    a, b, c = batch
    assert a["name"] == "台積電" and a["close"] == 106.0 and a["change"] == 1.0
    assert b["name"] == "2317" and b["change"] is None
    assert c["close"] is None
