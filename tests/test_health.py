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
