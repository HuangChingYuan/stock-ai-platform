from datetime import date, datetime

import pandas as pd

from core import llm
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


def test_revenue_yoy(db):
    rows = [{"stock_id": "2330", "year": y, "month": m, "revenue": rev}
            for y, m, rev in ((2024, 1, 100), (2024, 2, 0), (2025, 1, 150), (2025, 2, 80), (2025, 3, 90))]
    with session_scope() as s:
        repo.upsert(s, MonthlyRevenue, rows, keys=["stock_id", "year", "month"])
    with session_scope() as s:
        df = repo.revenue_df(s, "2330")
    yoy = dict(zip(zip(df["year"], df["month"]), df["yoy"]))
    assert yoy[(2025, 1)] == 50.0
    assert pd.isna(yoy[(2025, 2)])   # 去年同月營收為 0：不算年增率
    assert pd.isna(yoy[(2025, 3)])   # 沒有去年同月
    assert pd.isna(yoy[(2024, 1)])


def test_upsert_updates_existing_rows_and_cleans_nan(db):
    import numpy as np

    with session_scope() as s:
        repo.upsert(s, DailyPrice, [_price("2330", date(2026, 1, 2), 100.0)], keys=["stock_id", "date"])
        n = repo.upsert(s, DailyPrice, [{**_price("2330", date(2026, 1, 2), 101.0), "volume": np.int64(5), "open": np.nan},
                                        _price("2330", date(2026, 1, 3), 102.0)], keys=["stock_id", "date"])
        assert repo.upsert(s, DailyPrice, [], keys=["stock_id", "date"]) == 0
    assert n == 2
    with session_scope() as s:
        df = repo.prices_df(s, "2330")
    assert df["close"].tolist() == [101.0, 102.0]
    assert df["volume"].tolist() == [5, 100] and df["open"].isna().tolist() == [True, False]


def test_upsert_with_only_key_columns_ignores_duplicates(db):
    from core.models import Watch

    with session_scope() as s:
        repo.upsert(s, Watch, [{"chat_id": "1", "stock_id": "2330"}], keys=["chat_id", "stock_id"])
        repo.upsert(s, Watch, [{"chat_id": "1", "stock_id": "2330"}], keys=["chat_id", "stock_id"])
    with session_scope() as s:
        assert repo.watch_count(s, "1") == 1


def test_extract_json_handles_fences_and_extra_text():
    assert llm._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm._extract_json('結論如下：{"a": {"b": 2}} 以上') == {"a": {"b": 2}}
    import pytest
    with pytest.raises(ValueError):
        llm._extract_json("沒有 JSON")


def test_normalize_clamps_and_defaults():
    out = rpt._normalize({"action": "強力買進", "confidence": "150", "summary": "x" * 600,
                          "reasons": ["a", "b", "c", "d", "e"], "risks": "單一風險"})
    assert out["action"] == "觀望" and out["confidence"] == 100 and len(out["summary"]) == 500
    assert out["reasons"] == ["a", "b", "c", "d"] and out["risks"] == ["單一風險"]
    assert rpt._normalize({"action": "賣出", "confidence": "abc"})["confidence"] == 50
    assert rpt._normalize({"action": "買進", "confidence": -5, "reasons": None})["reasons"] == []
