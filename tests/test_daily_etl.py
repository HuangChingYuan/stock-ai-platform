import sys
from datetime import timedelta

import pytest

from core import repository as repo
from core.db import session_scope
from core.models import Report, Stock
from jobs import daily_etl


@pytest.fixture
def etl(db, monkeypatch):
    """把擷取、報告、推播換成假的，只測 main() 的流程判斷。"""
    calls = {"generate": [], "push": [], "refresh": 0}
    state = {"latest": repo.today()}
    monkeypatch.setattr(daily_etl, "sync_stock", lambda sid, days: state["latest"])

    def fake_refresh():
        calls["refresh"] += 1
        with session_scope() as session:
            repo.upsert(session, Stock, [{"stock_id": "2330", "name": "台積電", "industry": "半導體"}], keys=["stock_id"])
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
    state["latest"] = repo.today() - timedelta(days=1)
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
    day = repo.today()
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
    assert calls["refresh"] == 1 or repo.today().weekday() == 0
    monday = next(repo.today() - timedelta(days=i) for i in range(7) if (repo.today() - timedelta(days=i)).weekday() == 0)
    monkeypatch.setattr(daily_etl.repo, "today", lambda: monday)
    before = calls["refresh"]
    run("--force")
    assert calls["refresh"] == before + 1
