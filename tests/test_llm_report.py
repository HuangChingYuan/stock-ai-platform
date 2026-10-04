import time
from types import SimpleNamespace

import pytest

from core import llm
from core import report as rpt
from core import repository as repo
from core.db import session_scope
from core.models import DailyPrice


@pytest.fixture
def fake_openai(monkeypatch):
    """假的 OpenAI 用戶端：behaviour[provider] 決定該家回什麼。"""
    import openai

    calls, behaviour = [], {}

    class FakeClient:
        def __init__(self, base_url, **_):
            self.name = next(p.name for p in llm.PROVIDERS.values() if p.base_url == base_url)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **_):
            calls.append(self.name)
            result = behaviour[self.name]
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result))])

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(llm, "_down_until", {})
    monkeypatch.setattr(llm, "_last_call", {})
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return calls, behaviour


def test_failed_provider_is_skipped_afterwards(fake_openai):
    calls, behaviour = fake_openai
    behaviour.update(gemini=RuntimeError("429 quota"), groq='{"action": "觀望"}')
    assert llm.chat_json("s", "u")[1] == "groq"
    assert llm.chat_json("s", "u")[1] == "groq"
    assert calls == ["gemini", "groq", "groq"]  # 第二次不再先試 gemini


def test_bad_json_does_not_disable_provider(fake_openai):
    calls, behaviour = fake_openai
    behaviour.update(gemini="不是 JSON", groq='{"action": "觀望"}')
    llm.chat_json("s", "u")
    llm.chat_json("s", "u")
    assert calls == ["gemini", "groq", "gemini", "groq"]


def test_provider_comes_back_after_cooldown(fake_openai, monkeypatch):
    calls, behaviour = fake_openai
    behaviour.update(gemini=RuntimeError("timeout"), groq='{"action": "觀望"}')
    llm.chat_json("s", "u")
    llm._down_until["gemini"] = time.time() - 1
    behaviour["gemini"] = '{"action": "買進"}'
    assert llm.chat_json("s", "u")[1] == "gemini"


def _seed(sid="2330"):
    with session_scope() as s:
        repo.upsert(s, DailyPrice, [{"stock_id": sid, "date": repo.today(), "open": 1, "high": 1, "low": 1,
                                     "close": 1.0, "volume": 1}], keys=["stock_id", "date"])


def test_regenerate_has_cooldown_and_hourly_limit(db, monkeypatch):
    calls = []
    real = rpt.generate
    monkeypatch.setattr(rpt, "generate", lambda sid: calls.append(sid) or real(sid, use_llm=False))
    monkeypatch.setattr(rpt, "_regen_times", __import__("collections").deque())
    _seed("2330")
    _seed("2317")

    assert "不是" not in rpt.regenerate_markdown("2330") and calls == ["2330"]
    assert "分鐘內已產生過" in rpt.regenerate_markdown("2330") and calls == ["2330"]

    from core.config import get_settings
    from dataclasses import replace
    monkeypatch.setattr(rpt, "get_settings", lambda: replace(get_settings(), report_regen_per_hour=1))
    assert "已達上限" in rpt.regenerate_markdown("2317") and calls == ["2330"]


def test_regenerate_rejects_unknown_stock(db):
    assert "有效的股票代號" in rpt.regenerate_markdown("ABC")
    assert "還沒有 9999 的股價" in rpt.regenerate_markdown("9999")
