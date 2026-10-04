import os

# 必須在匯入 core.db 之前設定：CI 的 DATABASE_URL 指向正式的 Neon，測試一律改用記憶體內 SQLite
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test-secret"
os.environ["TELEGRAM_DEFAULT_CHAT_IDS"] = ""
for _k in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "CEREBRAS_API_KEY"):
    os.environ.pop(_k, None)  # CI 有設真的金鑰；測試不可以呼叫 LLM

import pytest  # noqa: E402


@pytest.fixture
def db():
    from core import models  # noqa: F401
    from core.db import Base, engine

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
