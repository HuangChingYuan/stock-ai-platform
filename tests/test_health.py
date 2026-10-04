import importlib

import pytest

pytest.importorskip("fastapi")  # 排程的 requirements-jobs.txt 不含 fastapi

from fastapi.testclient import TestClient  # noqa: E402

from core.config import get_settings  # noqa: E402


@pytest.fixture
def main(db, monkeypatch):
    """不載入 Gradio／Dash，只測健康檢查。"""
    monkeypatch.setenv("ENABLE_GRADIO", "false")
    monkeypatch.setenv("ENABLE_DASH", "false")
    get_settings.cache_clear()
    from app import main

    yield importlib.reload(main)
    get_settings.cache_clear()


def test_health_does_not_touch_database(main, monkeypatch):
    c = TestClient(main.app)
    assert "price_rows" in c.get("/health/db").json()

    def no_db():
        raise AssertionError("/health 不可查資料庫，否則 Neon 無法閒置暫停")

    monkeypatch.setattr(main, "session_scope", no_db)
    assert c.get("/health").json()["status"] == "ok"


def test_telegram_webhook_synced_only_on_render(main, monkeypatch):
    import threading

    done = threading.Event()
    monkeypatch.setattr(main, "sync_webhook", lambda: done.set() or "ok")  # 在背景執行緒執行，不擋啟動
    monkeypatch.delenv("RENDER", raising=False)
    with TestClient(main.app):
        pass
    assert not done.wait(0.2)  # 本機開發不可蓋掉正式 webhook
    monkeypatch.setenv("RENDER", "true")
    with TestClient(main.app):
        pass
    assert done.wait(5)


def test_root_manifest_served_for_gradio(main):
    # Gradio 頁面固定向網域根目錄要 /manifest.json；缺少會在瀏覽器主控台出現 404
    r = TestClient(main.app).get("/manifest.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    assert r.json()["start_url"] == "/ui/gradio/"
