"""集中讀取環境變數。本機開發可放在 .env（由 python-dotenv 或 IDE 載入），部署時設在各平台的 Secrets。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _list(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    finmind_token: str
    watchlist: list[str]
    llm_providers: list[str]
    llm_min_interval: float
    telegram_bot_token: str
    telegram_webhook_secret: str
    telegram_default_chat_ids: list[str]
    cors_origins: list[str]
    enable_gradio: bool
    enable_dash: bool
    public_base_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./local.db"),
        finmind_token=os.getenv("FINMIND_TOKEN", ""),
        watchlist=_list(os.getenv("WATCHLIST", "2330,2317,2454")),
        llm_providers=_list(os.getenv("LLM_PROVIDERS", "gemini,groq,openrouter,cerebras")),
        llm_min_interval=float(os.getenv("LLM_MIN_INTERVAL", "6")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", ""),
        telegram_default_chat_ids=_list(os.getenv("TELEGRAM_DEFAULT_CHAT_IDS", "")),
        cors_origins=_list(os.getenv("CORS_ORIGINS", "*")),
        enable_gradio=_bool(os.getenv("ENABLE_GRADIO", "true")),
        enable_dash=_bool(os.getenv("ENABLE_DASH", "true")),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
    )
