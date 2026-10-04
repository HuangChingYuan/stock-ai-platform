import os

# 必須在匯入 core.db 之前設定：CI 的 DATABASE_URL 指向正式的 Neon，測試一律改用記憶體內 SQLite
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_WEBHOOK_SECRET"] = ""

import pytest  # noqa: E402


@pytest.fixture
def db():
    from core import models  # noqa: F401
    from core.db import Base, engine

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
