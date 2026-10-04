from dataclasses import replace

import pytest

pytest.importorskip("fastapi")  # 排程的 requirements-jobs.txt 不含 fastapi

from core import repository as repo  # noqa: E402
from core.db import session_scope  # noqa: E402
from core.models import DailyPrice, Report, Stock, Watch  # noqa: E402


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

    def boom(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(tg, "handle", boom)
    app = FastAPI()
    app.include_router(tg.router)
    r = TestClient(app).post("/telegram/webhook", json={"message": {"text": "/price 2330", "chat": {"id": 1}}},
                             headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"})
    assert r.status_code == 200 and sent == ["處理時發生錯誤，請稍後再試。"]


def test_webhook_secret_is_required(bot, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.config import get_settings

    tg, sent = bot
    app = FastAPI()
    app.include_router(tg.router)
    client = TestClient(app)
    body = {"message": {"text": "/help", "chat": {"id": 1}}}
    assert client.post("/telegram/webhook", json=body).status_code == 403
    assert client.post("/telegram/webhook", json=body,
                       headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).status_code == 403
    monkeypatch.setattr(tg, "get_settings", lambda: replace(get_settings(), telegram_webhook_secret=""))
    assert client.post("/telegram/webhook", json=body).status_code == 503
    assert sent == []


def test_api_sets_cache_header_only_on_success(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    ok = client.get("/api/stocks")
    assert ok.status_code == 200 and ok.headers["cache-control"] == "public, max-age=300"
    missing = client.get("/api/stocks/2330/report")
    assert missing.status_code == 404 and "cache-control" not in missing.headers


def _stocks():
    with session_scope() as s:
        repo.upsert(s, Stock, [{"stock_id": sid, "name": name, "industry": None} for sid, name in
                               (("2330", "台積電"), ("2303", "聯電"), ("2882", "國泰金"), ("2834", "臺企銀"),
                                ("00878", "國泰永續高股息"))], keys=["stock_id"])
        repo.upsert(s, DailyPrice, [{"stock_id": "2330", "date": repo.today(), "open": 1, "high": 1, "low": 1,
                                     "close": 1000.0, "volume": 1}], keys=["stock_id", "date"])


def test_start_shows_chat_id(bot):
    tg, _ = bot
    assert "你的 chat id：42" in tg.handle("42", "/start")
    assert "chat id" not in tg.handle("42", "/help")


def test_plain_text_code_or_name_gives_quote(bot):
    tg, _ = bot
    _stocks()
    for text in ("2330", "台積電", "/price 台積電"):
        reply = tg.handle("1", text)
        assert "2330 台積電" in reply and "收盤 1000" in reply
    assert "AI 報告：每個交易日盤後產生" in tg.handle("1", "台積")  # 名稱開頭相符


def test_name_lookup_ambiguous_or_missing(bot):
    tg, _ = bot
    _stocks()
    reply = tg.handle("1", "國泰")
    assert "好幾檔" in reply and "2882 國泰金" in reply and "00878" in reply
    assert "找不到名稱含「鴻海」" in tg.handle("1", "鴻海")
    assert "/watch 2303" in tg.handle("1", "聯電")  # 還沒有股價：提示加入自選股


def test_quote_shows_latest_ai_report(bot):
    tg, _ = bot
    _stocks()
    with session_scope() as s:
        repo.upsert(s, Report, [{"stock_id": "2330", "date": repo.today(), "action": "買進", "confidence": 70,
                                 "summary": "趨勢向上", "reasons": "", "risks": "", "provider": "groq", "model": "x"}],
                    keys=["stock_id", "date"])
    reply = tg.handle("1", "2330")
    assert "買進（信心 70）趨勢向上" in reply and "/report 2330" in reply


def test_watch_new_stock_requests_immediate_fetch(bot):
    tg, _ = bot
    _stocks()
    fetch = []
    assert "正在下載資料" in tg.handle("1", "/watch 聯電", fetch) and fetch == ["2303"]
    fetch.clear()
    assert "盤後推播" in tg.handle("1", "/watch 2330", fetch) and fetch == []  # 已有股價不必下載
    assert "查無股票 9999" in tg.handle("1", "/watch 9999", fetch)
    with session_scope() as s:
        assert {w.stock_id for w in s.query(Watch).filter_by(chat_id="1")} == {"2303", "2330"}
    reply = tg.handle("1", "/list")
    assert "2330 台積電 1000" in reply and "2303 聯電（資料下載中或尚無股價）" in reply
    assert "已移除 2303" in tg.handle("1", "/unwatch 聯電")
    assert "自選股裡沒有" in tg.handle("1", "/unwatch 2303")


def test_webhook_fetches_in_background_after_reply(bot, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    tg, sent = bot
    _stocks()
    fetched = []
    monkeypatch.setattr(tg, "fetch_and_notify", lambda chat, sid: fetched.append((chat, sid)))
    app = FastAPI()
    app.include_router(tg.router)
    r = TestClient(app).post("/telegram/webhook", json={"message": {"text": "/watch 2303", "chat": {"id": 7}}},
                             headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"})
    assert r.status_code == 200 and "正在下載資料" in sent[0] and fetched == [("7", "2303")]


def test_webhook_ignores_plain_text_in_groups(bot):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    tg, sent = bot
    _stocks()
    app = FastAPI()
    app.include_router(tg.router)
    client = TestClient(app)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
    group = {"id": -5, "type": "group"}
    client.post("/telegram/webhook", json={"message": {"text": "大家好", "chat": group}}, headers=headers)
    assert sent == []
    client.post("/telegram/webhook", json={"message": {"text": "/price 2330", "chat": group}}, headers=headers)
    client.post("/telegram/webhook", json={"message": {"text": "2330", "chat": {"id": 5, "type": "private"}}},
                headers=headers)
    assert len(sent) == 2 and all("2330 台積電" in x for x in sent)


def test_fetch_and_notify(bot, monkeypatch):
    from jobs import daily_etl

    tg, sent = bot
    _stocks()
    monkeypatch.setattr(daily_etl, "sync_stock", lambda sid, days: repo.today())
    tg.fetch_and_notify("1", "2330")

    def exhausted(sid, days):
        raise tg.ds.QuotaExceededError("HTTP 402")

    monkeypatch.setattr(daily_etl, "sync_stock", exhausted)
    tg.fetch_and_notify("1", "2330")
    monkeypatch.setattr(daily_etl, "sync_stock", lambda sid, days: None)
    tg.fetch_and_notify("1", "2330")
    assert sent[0].startswith("資料已就緒\n2330 台積電") and "使用量已達上限" in sent[1] and "沒有 2330" in sent[2]


def test_sync_webhook_sets_url_secret_and_commands(bot, monkeypatch):
    from core.config import get_settings

    tg, _ = bot
    assert tg.sync_webhook().startswith("略過")
    calls = []

    class Ok:
        def raise_for_status(self):
            return self

    monkeypatch.setattr(tg.httpx, "post", lambda url, json, timeout: calls.append((url, json)) or Ok())
    monkeypatch.setattr(tg, "get_settings", lambda: replace(
        get_settings(), telegram_bot_token="T", public_base_url="https://api.example.com"))
    assert "https://api.example.com/telegram/webhook" in tg.sync_webhook()
    (hook_url, hook), (cmd_url, cmds) = calls
    assert hook_url.endswith("/botT/setWebhook") and hook["secret_token"] == "test-secret"
    assert cmd_url.endswith("/setMyCommands") and {c["command"] for c in cmds["commands"]} >= {"report", "watch"}


def test_public_base_url_falls_back_to_render(monkeypatch):
    from core.config import get_settings

    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://stock-ai-api.onrender.com/")
    get_settings.cache_clear()
    try:
        assert get_settings().public_base_url == "https://stock-ai-api.onrender.com"
    finally:
        get_settings.cache_clear()
