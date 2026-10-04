from datetime import date, timedelta

import pytest

from core import backtest as bt
from core import repository as repo
from core.db import session_scope
from core.models import DailyPrice, Report


def _seed(closes, reports, sid="2330"):
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(len(closes))]
    with session_scope() as s:
        repo.upsert(s, DailyPrice, [{"stock_id": sid, "date": d, "open": c, "high": c, "low": c, "close": c,
                                     "volume": 1} for d, c in zip(days, closes)], keys=["stock_id", "date"])
        repo.upsert(s, Report, [{"stock_id": sid, "date": days[i], "action": a, "confidence": 60, "summary": "",
                                 "reasons": "", "risks": "", "provider": p, "model": "m", "rule_action": ra,
                                 "rule_confidence": 50 if ra else None} for i, a, p, ra in reports],
                    keys=["stock_id", "date"])
    return days


def test_forward_returns_and_win_rates(db):
    # 收盤 100 → 110 → 99 → 99 → 120
    _seed([100, 110, 99, 99, 120], [
        (0, "買進", "gemini", "賣出"),   # 1 日後 +10%：LLM 贏、規則輸
        (1, "賣出", "gemini", "觀望"),   # 1 日後 -10%：LLM 贏
        (2, "觀望", "rules", None),      # 只有規則式
        (4, "買進", "gemini", "買進"),   # 沒有之後的股價，不計入
    ])
    with session_scope() as s:
        frame = bt.signals_frame(s, horizons=(1,))
    assert len(frame) == 7 and frame["ret_1"].isna().sum() == 2
    assert frame.loc[0, "ret_1"] == pytest.approx(0.1)

    paired = bt.summarize(frame, 1).set_index("來源")
    assert list(paired.index) == ["LLM", "規則式", "每天都買"]
    assert paired.loc["LLM", ["樣本", "買進", "賣出", "勝率%"]].tolist() == [2, 1, 1, 100.0]
    assert paired.loc["規則式", ["樣本", "賣出", "觀望", "勝率%"]].tolist() == [2, 1, 1, 0.0]
    assert paired.loc["每天都買", ["樣本", "勝率%"]].tolist() == [2, 50.0]

    every = bt.summarize(frame, 1, paired=False).set_index("來源")
    assert every.loc["規則式", "樣本"] == 3  # 加上只有規則式報告的那天


def test_old_reports_recompute_rule_action(db, monkeypatch):
    seen = []
    monkeypatch.setattr(bt.ta, "rule_score", lambda ind, prices: seen.append(prices["date"].max()) or ("買進", 55, []))
    days = _seed([100, 101, 102], [(1, "觀望", "groq", None)])
    with session_scope() as s:
        frame = bt.signals_frame(s, horizons=(1,))
    rules = frame[frame["source"] == "rules"].iloc[0]
    assert (rules["action"], rules["confidence"]) == ("買進", 55)
    assert seen == [days[1]]  # 只用報告當天以前的資料，不偷看未來


def test_summarize_empty(db):
    with session_scope() as s:
        assert bt.summarize(bt.signals_frame(s), 5).empty
