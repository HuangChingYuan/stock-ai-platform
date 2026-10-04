import pytest

pytest.importorskip("fastapi")  # 排程的 requirements-jobs.txt 不含 fastapi

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import data_sources as ds  # noqa: E402
from core import repository as repo  # noqa: E402
from core.db import session_scope  # noqa: E402
from core.models import DailyPrice, Stock, Watch  # noqa: E402


@pytest.fixture
def client(db, monkeypatch):
    """只掛 API router，不載入 Gradio／Dash；FinMind 擷取換成假的。"""
    from app import api
    from jobs import daily_etl

    calls = []

    def fake_sync(sid, days):
        calls.append(sid)
        if sid == "9999":  # FinMind 查無資料
            return None
        with session_scope() as s:
            repo.upsert(s, DailyPrice, [{"stock_id": sid, "date": repo.today(), "open": 1, "high": 1, "low": 1,
                                         "close": 50.0, "volume": 1}], keys=["stock_id", "date"])
        return repo.today()

    monkeypatch.setattr(daily_etl, "sync_stock", fake_sync)
    with session_scope() as s:
        repo.upsert(s, Stock, [{"stock_id": sid, "name": f"名稱{sid}", "industry": None}
                               for sid in ("2330", "2603", "9999", *[str(3000 + i) for i in range(20)])],
                    keys=["stock_id"])
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(api.watch_router)
    return TestClient(app), calls


def pwa_watch_ids():
    with session_scope() as s:
        return sorted(w.stock_id for w in s.query(Watch).filter_by(chat_id=repo.PWA_CHAT_ID))


def test_add_fetches_when_no_data_and_lists_as_removable(client):
    c, calls = client
    r = c.post("/api/watchlist/2603")
    assert r.status_code == 200
    body = r.json()
    assert body["fetched"] is True and body["name"] == "名稱2603" and body["close"] == 50.0 and body["removable"]
    assert "cache-control" not in r.headers
    assert calls == ["2603"] and pwa_watch_ids() == ["2603"]
    listed = {q["stock_id"]: q for q in c.get("/api/stocks").json()}
    assert listed["2603"]["removable"] is True and listed["2330"]["removable"] is False


def test_add_skips_fetch_when_data_exists(client):
    c, calls = client
    c.post("/api/watchlist/2603")
    r = c.post("/api/watchlist/2603")
    assert r.status_code == 200 and r.json()["fetched"] is False and calls == ["2603"]


def test_env_watchlist_stock_is_not_stored(client):
    c, _ = client
    with session_scope() as s:
        repo.upsert(s, DailyPrice, [{"stock_id": "2330", "date": repo.today(), "open": 1, "high": 1, "low": 1,
                                     "close": 1.0, "volume": 1}], keys=["stock_id", "date"])
    r = c.post("/api/watchlist/2330")
    assert r.status_code == 200 and r.json()["removable"] is False and pwa_watch_ids() == []


def test_rejects_invalid_unknown_and_no_data(client):
    c, calls = client
    assert c.post("/api/watchlist/ABC").status_code == 422
    assert c.post("/api/watchlist/1234").status_code == 404  # 不在股票清單
    assert c.post("/api/watchlist/9999").status_code == 404  # FinMind 沒有股價
    assert calls == ["9999"] and pwa_watch_ids() == []


def test_data_source_error_returns_502(client, monkeypatch):
    c, _ = client
    from jobs import daily_etl

    def broken(sid, days):
        raise ds.DataSourceError("HTTP 503")

    monkeypatch.setattr(daily_etl, "sync_stock", broken)
    assert c.post("/api/watchlist/2603").status_code == 502 and pwa_watch_ids() == []


def test_quota_error_returns_503(client, monkeypatch):
    c, _ = client
    from jobs import daily_etl

    def exhausted(sid, days):
        raise ds.QuotaExceededError("HTTP 402")

    monkeypatch.setattr(daily_etl, "sync_stock", exhausted)
    assert c.post("/api/watchlist/2603").status_code == 503 and pwa_watch_ids() == []


def test_limit_and_remove(client):
    c, _ = client
    from core.config import get_settings

    limit = get_settings().max_watch_per_chat
    for i in range(limit):
        assert c.post(f"/api/watchlist/{3000 + i}").status_code == 200
    assert c.post("/api/watchlist/2603").status_code == 409
    assert c.post("/api/watchlist/3000").status_code == 200  # 已在清單中的不受上限影響
    assert c.delete("/api/watchlist/3000").status_code == 200
    assert c.post("/api/watchlist/2603").status_code == 200
    assert "3000" not in pwa_watch_ids() and "2603" in pwa_watch_ids()


def test_push_digest_skips_pwa_chat(db, monkeypatch):
    from jobs import daily_etl

    with session_scope() as s:
        repo.upsert(s, DailyPrice, [{"stock_id": "2603", "date": repo.today(), "open": 1, "high": 1, "low": 1,
                                     "close": 1.0, "volume": 1}], keys=["stock_id", "date"])
        repo.upsert(s, Watch, [{"chat_id": repo.PWA_CHAT_ID, "stock_id": "2603"},
                               {"chat_id": "123", "stock_id": "2603"}], keys=["chat_id", "stock_id"])
    sent = []
    monkeypatch.setattr(daily_etl, "send_telegram", lambda chat, text: sent.append(chat))
    daily_etl.push_digest({"2603": {"action": "觀望", "confidence": 50, "summary": "", "date": str(repo.today())}})
    assert sent == ["123"]


def test_industries_and_stocks_by_industry(client):
    c, _ = client
    with session_scope() as s:
        repo.upsert(s, Stock, [{"stock_id": "2330", "name": "台積電", "industry": "半導體業", "market": "twse"},
                               {"stock_id": "2303", "name": "聯電", "industry": "半導體業", "market": "twse"},
                               {"stock_id": "6488", "name": "環球晶", "industry": "半導體業", "market": "tpex"},
                               {"stock_id": "2603", "name": "長榮", "industry": "航運業", "market": "twse"}],
                    keys=["stock_id"])
    r = c.get("/api/industries")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=300"
    assert r.json() == [{"market": "twse", "industry": "半導體業", "count": 2},  # 沒有產業別的不列
                        {"market": "tpex", "industry": "半導體業", "count": 1},
                        {"market": "twse", "industry": "航運業", "count": 1}]
    r = c.get("/api/industries/stocks", params={"industry": "半導體業", "market": "twse"})
    assert r.json() == [{"stock_id": "2303", "name": "聯電"}, {"stock_id": "2330", "name": "台積電"}]
    r = c.get("/api/industries/stocks", params={"industry": "半導體業", "market": "tpex"})
    assert r.json() == [{"stock_id": "6488", "name": "環球晶"}]
    assert len(c.get("/api/industries/stocks", params={"industry": "半導體業"}).json()) == 3
    assert c.get("/api/industries/stocks", params={"industry": "不存在"}).json() == []
    assert c.get("/api/industries/stocks").status_code == 422
