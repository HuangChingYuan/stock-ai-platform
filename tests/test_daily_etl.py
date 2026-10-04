import sys
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from core import repository as repo
from core.db import session_scope
from core.models import Report, Stock
from jobs import daily_etl


@pytest.fixture
def etl(db, monkeypatch):
    """把擷取、報告、推播換成假的，只測 main() 的流程判斷。"""
    calls = {"generate": [], "push": [], "refresh": 0}
    state = {"latest": repo.trading_day()}
    monkeypatch.setattr(daily_etl, "sync_stock", lambda sid, days: state["latest"])

    def fake_refresh():
        calls["refresh"] += 1
        with session_scope() as session:
            repo.upsert(session, Stock, [{"stock_id": "2330", "name": "台積電", "industry": "半導體", "market": "twse"}],
                        keys=["stock_id"])
        return 1

    monkeypatch.setattr(daily_etl, "refresh_stock_info", fake_refresh)

    def fake_generate(sid, use_llm=True):
        calls["generate"].append(sid)
        with session_scope() as session:
            repo.upsert(session, Report, [{"stock_id": sid, "date": state["latest"], "action": "觀望", "confidence": 50,
                                           "summary": "", "reasons": "", "risks": "", "provider": "rules", "model": "x"}],
                        keys=["stock_id", "date"])
        return {"action": "觀望", "confidence": 50, "summary": "", "provider": "rules", "date": str(state["latest"])}

    monkeypatch.setattr(daily_etl.rpt, "generate", fake_generate)
    monkeypatch.setattr(daily_etl, "push_digest", lambda reports: calls["push"].extend(reports))

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["daily_etl", "--stocks", "2330,BAD", *args])
        daily_etl.main()
        return calls

    return run, state, calls


def test_trading_day_generates_and_skips_invalid_ids(etl):
    run, _, calls = etl
    run()
    assert calls["generate"] == ["2330"] and calls["push"] == ["2330"]


def test_holiday_skips_report_and_push(etl):
    run, state, calls = etl
    state["latest"] = repo.trading_day() - timedelta(days=1)
    run()
    assert calls["generate"] == [] and calls["push"] == []


def test_rerun_same_day_does_not_push_twice(etl):
    run, _, calls = etl
    run()
    run()
    assert calls["push"] == ["2330"]
    run("--force")
    assert calls["push"] == ["2330", "2330"]


def test_push_digest_sends_one_message_per_chat(db, monkeypatch):
    from core.models import DailyPrice, Watch

    sent = []
    monkeypatch.setattr(daily_etl, "send_telegram", lambda chat, text: sent.append((chat, text)))
    day = repo.trading_day()
    with session_scope() as s:
        repo.upsert(s, DailyPrice, [{"stock_id": sid, "date": day, "open": 1, "high": 1, "low": 1, "close": 10.0,
                                     "volume": 1} for sid in ("2330", "2317", "2454")], keys=["stock_id", "date"])
        repo.upsert(s, Watch, [{"chat_id": "a", "stock_id": "2330"}, {"chat_id": "a", "stock_id": "2317"},
                               {"chat_id": "b", "stock_id": "2454"}], keys=["chat_id", "stock_id"])
    rep = {"action": "觀望", "confidence": 50, "summary": "摘要", "date": str(day)}
    daily_etl.push_digest({sid: rep for sid in ("2330", "2317", "2454")})
    by_chat = {chat: text for chat, text in sent}
    assert len(sent) == 2
    assert "2330" in by_chat["a"] and "2317" in by_chat["a"] and "2454" not in by_chat["a"]
    assert "2454" in by_chat["b"] and "2330" not in by_chat["b"]


def test_chunk_messages_splits_long_digest():
    blocks = ["x" * 900 for _ in range(10)]
    parts = daily_etl.chunk_messages("頭", blocks, "尾", limit=4000)
    assert len(parts) == 3
    assert all(len(p) <= 4000 and p.startswith("頭") and p.endswith("尾") for p in parts)
    assert sum(p.count("x" * 900) for p in parts) == 10


def test_stock_names_refresh_when_empty_or_monday(etl, monkeypatch):
    run, _, calls = etl
    run()
    assert calls["refresh"] == 1  # 股票清單是空的：立刻更新
    run("--force")
    assert calls["refresh"] == 1 or repo.trading_day().weekday() == 0
    monday = next(repo.trading_day() - timedelta(days=i) for i in range(7) if (repo.trading_day() - timedelta(days=i)).weekday() == 0)
    monkeypatch.setattr(daily_etl.repo, "trading_day", lambda: monday)
    before = calls["refresh"]
    run("--force")
    assert calls["refresh"] == before + 1


def test_stock_names_refresh_when_target_has_no_name(etl):
    run, _, calls = etl
    with session_scope() as session:  # 資料表不是空的，但要處理的股票沒有名稱
        repo.upsert(session, Stock, [{"stock_id": "2330", "name": None, "industry": None}], keys=["stock_id"])
    run("--force")
    assert calls["refresh"] == 1
    run("--force")
    assert calls["refresh"] == 1 or repo.trading_day().weekday() == 0


def test_stock_info_refresh_when_target_has_no_market(etl):
    run, _, calls = etl
    with session_scope() as session:  # 新增 market 欄位前抓的清單：有名稱但沒有上市櫃別
        repo.upsert(session, Stock, [{"stock_id": "2330", "name": "台積電", "industry": "半導體"}], keys=["stock_id"])
    run("--force")
    assert calls["refresh"] == 1
    run("--force")
    assert calls["refresh"] == 1 or repo.trading_day().weekday() == 0


def test_trading_day_counts_early_morning_as_previous_day():
    tpe = repo.TAIPEI
    assert repo.trading_day(datetime(2026, 9, 29, 2, 14, tzinfo=tpe)) == date(2026, 9, 28)  # 延遲過午夜
    assert repo.trading_day(datetime(2026, 9, 28, 18, 30, tzinfo=tpe)) == date(2026, 9, 28)
    assert repo.trading_day(datetime(2026, 9, 28, 16, 20, tzinfo=timezone.utc)) == date(2026, 9, 28)  # 台灣 00:20
    assert repo.trading_day(datetime(2026, 9, 29, 6, 0, tzinfo=tpe)) == date(2026, 9, 29)


def test_refresh_stock_info_skips_indices(db, monkeypatch):
    import pandas as pd

    raw = pd.DataFrame([
        {"stock_id": "2330", "stock_name": "台積電", "industry_category": "半導體業", "type": "twse", "date": "2026-10-02"},
        {"stock_id": "00878", "stock_name": "國泰永續高股息", "industry_category": "ETF", "type": "twse", "date": "2026-10-02"},
        {"stock_id": "6488", "stock_name": "環球晶", "industry_category": "半導體業", "type": "tpex", "date": "2026-10-02"},
        {"stock_id": "2618", "stock_name": "長榮航", "industry_category": "航運業", "type": "tpex", "date": "2010-01-01"},
        {"stock_id": "2618", "stock_name": "長榮航", "industry_category": "航運業", "type": "twse", "date": "2026-10-02"},
        {"stock_id": "ElectronicProductsDistribution", "stock_name": "電子通路類指數", "industry_category": "Index",
         "type": "twse", "date": "2026-10-02"},
        {"stock_id": "TPEx", "stock_name": "櫃買指數", "industry_category": "大盤", "type": "tpex", "date": "2026-10-02"},
    ])
    monkeypatch.setattr(daily_etl.ds, "_finmind", lambda *a, **k: raw)
    assert daily_etl.refresh_stock_info() == 4
    with session_scope() as session:
        markets = dict(session.execute(select(Stock.stock_id, Stock.market)).all())
    # 上櫃轉上市（2618）以最新一筆為準
    assert markets == {"00878": "twse", "2330": "twse", "2618": "twse", "6488": "tpex"}


def test_refresh_stock_info_failure_does_not_stop_etl(db, monkeypatch):
    import pandas as pd

    monkeypatch.setattr(daily_etl.ds, "fetch_stock_info",
                        lambda: pd.DataFrame([{"stock_id": "2330", "name": "台積電", "industry": "半導體業"}]))

    def broken_upsert(*a, **k):
        raise RuntimeError("value too long for type character varying(10)")

    monkeypatch.setattr(daily_etl.repo, "upsert", broken_upsert)
    assert daily_etl.refresh_stock_info() == 0


def test_fetch_institutional_sums_by_investor_type(monkeypatch):
    import pandas as pd

    raw = pd.DataFrame([
        {"date": "2026-09-28", "stock_id": "2330", "name": "Foreign_Investor", "buy": 5000, "sell": 1000},
        {"date": "2026-09-28", "stock_id": "2330", "name": "Foreign_Dealer_Self", "buy": 0, "sell": 500},
        {"date": "2026-09-28", "stock_id": "2330", "name": "Investment_Trust", "buy": 100, "sell": 300},
        {"date": "2026-09-28", "stock_id": "2330", "name": "Dealer_self", "buy": 10, "sell": 0},
        {"date": "2026-09-28", "stock_id": "2330", "name": "Dealer_Hedging", "buy": 0, "sell": 30},
        {"date": "2026-09-28", "stock_id": "2330", "name": "total", "buy": 9, "sell": 9},
        {"date": "2026-09-29", "stock_id": "2330", "name": "Foreign_Investor", "buy": 1, "sell": 2},
    ])
    monkeypatch.setattr(daily_etl.ds, "_finmind", lambda *a, **k: raw)
    rows = daily_etl.ds.fetch_institutional("2330", date(2026, 9, 1)).to_dict("records")
    assert rows == [
        {"date": date(2026, 9, 28), "foreign_net": 3500, "trust_net": -200, "dealer_net": -20},
        {"date": date(2026, 9, 29), "foreign_net": -1, "trust_net": None, "dealer_net": None},
    ]


def test_optional_fetch_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(daily_etl.time, "sleep", lambda s: None)

    def broken(stock_id, start):
        raise daily_etl.ds.DataSourceError("HTTP 402")

    assert daily_etl._optional(broken, "2330", date(2026, 9, 1)).empty


def test_quota_error_is_not_retried(monkeypatch):
    calls = []

    class Resp:
        status_code = 402

    monkeypatch.setattr(daily_etl.ds.requests, "get", lambda *a, **k: calls.append(1) or Resp())
    monkeypatch.setattr(daily_etl.ds.time, "sleep", lambda s: None)
    with pytest.raises(daily_etl.ds.QuotaExceededError):
        daily_etl.ds.fetch_prices("2330", date(2026, 9, 1))
    assert calls == [1]  # 402 時重試只會延長 IP 封鎖


def test_optional_fetch_reraises_quota_error(monkeypatch):
    monkeypatch.setattr(daily_etl.time, "sleep", lambda s: None)

    def exhausted(stock_id, start):
        raise daily_etl.ds.QuotaExceededError("HTTP 402")

    with pytest.raises(daily_etl.ds.QuotaExceededError):
        daily_etl._optional(exhausted, "2330", date(2026, 9, 1))


def test_quota_error_stops_batch_but_pushes_done_reports(etl, monkeypatch):
    run, state, calls = etl
    synced = []

    def fake_sync(sid, days):
        synced.append(sid)
        if sid == "2317":
            raise daily_etl.ds.QuotaExceededError("HTTP 402")
        return state["latest"]

    monkeypatch.setattr(daily_etl, "sync_stock", fake_sync)
    monkeypatch.setattr(sys, "argv", ["daily_etl", "--stocks", "2330,2317,2454"])
    with pytest.raises(SystemExit, match="2317, 2454"):
        daily_etl.main()
    assert synced == ["2330", "2317"] and calls["push"] == ["2330"]
