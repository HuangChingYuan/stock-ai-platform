import sys
from datetime import date, timedelta

import pytest

from core import repository as repo
from core.db import session_scope
from core.models import Report
from jobs import daily_etl


@pytest.fixture
def etl(db, monkeypatch):
    """把擷取、報告、推播換成假的，只測 main() 的流程判斷。"""
    calls = {"generate": [], "push": []}
    state = {"latest": repo.today()}
    monkeypatch.setattr(daily_etl, "sync_stock", lambda sid, days: state["latest"])

    def fake_generate(session, sid, use_llm=True):
        calls["generate"].append(sid)
        repo.upsert(session, Report, [{"stock_id": sid, "date": state["latest"], "action": "觀望", "confidence": 50,
                                       "summary": "", "reasons": "", "risks": "", "provider": "rules", "model": "x"}],
                    keys=["stock_id", "date"])
        return {"action": "觀望", "confidence": 50, "summary": "", "provider": "rules"}

    monkeypatch.setattr(daily_etl.rpt, "generate", fake_generate)
    monkeypatch.setattr(daily_etl, "push", lambda sid, report: calls["push"].append(sid))

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
