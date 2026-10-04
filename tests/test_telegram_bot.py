import pytest

pytest.importorskip("fastapi")  # 排程的 requirements-jobs.txt 不含 fastapi

from core import repository as repo  # noqa: E402
from core.db import session_scope  # noqa: E402
from core.models import DailyPrice  # noqa: E402


@pytest.fixture
def bot(db, monkeypatch):
    from app import telegram_bot

    sent = []
    monkeypatch.setattr(telegram_bot, "send_telegram", lambda chat, text: sent.append(text))
    return telegram_bot, sent


def test_rejects_invalid_stock_id(bot):
    tg, _ = bot
    assert "不是有效的股票代號" in tg.handle("1", "/watch ABCDEFGHIJKLMNOP")
    assert "不是有效的股票代號" in tg.handle("1", "/price 12")


def test_watch_limit_per_chat(bot):
    tg, _ = bot
    from core.config import get_settings

    limit = get_settings().max_watch_per_chat
    for i in range(limit):
        assert "已加入" in tg.handle("1", f"/watch {2300 + i}")
    assert "最多" in tg.handle("1", "/watch 9999")
    assert "已加入" in tg.handle("1", "/watch 2300")  # 已在清單中的不受上限影響
    assert "已加入" in tg.handle("2", "/watch 9999")  # 上限是每個對話各自計算


def test_price_without_volume(bot):
    tg, _ = bot
    with session_scope() as s:
        repo.upsert(s, DailyPrice, [{"stock_id": "2330", "date": repo.today(), "open": 1, "high": 1, "low": 1,
                                     "close": 1000.0, "volume": None}], keys=["stock_id", "date"])
    reply = tg.handle("1", "/price 2330")
    assert "收盤 1000" in reply and "成交量" not in reply


def test_webhook_returns_200_when_handler_fails(bot, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    tg, sent = bot

    def boom(chat_id, text):
        raise RuntimeError("db down")

    monkeypatch.setattr(tg, "handle", boom)
    app = FastAPI()
    app.include_router(tg.router)
    r = TestClient(app).post("/telegram/webhook", json={"message": {"text": "/price 2330", "chat": {"id": 1}}})
    assert r.status_code == 200 and sent == ["處理時發生錯誤，請稍後再試。"]
