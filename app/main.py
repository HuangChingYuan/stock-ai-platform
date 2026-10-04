"""FastAPI 主程式：一個 Render 免費 Web Service 同時提供 API 與多種 Python UI。

  /health          喚醒與健康檢查
  /api/*           JSON API
  /telegram/*      Telegram webhook
  /ui/gradio/      Gradio（個股 K 線＋AI 報告）
  /ui/dash/        Dash（月營收）
Streamlit 無法掛進 ASGI，另外部署在 Streamlit Community Cloud，由 PWA 外殼以 iframe 組合。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.api import router as api_router
from app.telegram_bot import router as telegram_router
from core.config import get_settings
from core.db import engine, init_db, session_scope
from core.models import DailyPrice

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")  # 不送使用統計
logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()  # 資料表不存在才建立
    yield


app = FastAPI(title="台股 AI 分析平台", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET"], allow_headers=["*"])
app.include_router(api_router)
app.include_router(telegram_router)

UIS: dict[str, str] = {}


@app.get("/health")
def health():
    with session_scope() as s:
        rows = s.scalar(select(func.count()).select_from(DailyPrice))
    return {"status": "ok", "db": engine.dialect.name, "price_rows": rows, "uis": UIS}


@app.get("/")
def index():
    return {"name": "台股 AI 分析平台", "docs": "/docs", "uis": UIS}


# ---- 多種 Python UI 掛載（可用環境變數關閉，節省 512MB 記憶體）----
if settings.enable_dash:
    from a2wsgi import WSGIMiddleware

    from app.ui.dash_app import build as build_dash

    app.mount("/ui/dash", WSGIMiddleware(build_dash("/ui/dash/").server))
    UIS["dash"] = "/ui/dash/"

if settings.enable_gradio:
    import gradio as gr

    from app.ui.gradio_app import CSS, build as build_gradio

    app = gr.mount_gradio_app(app, build_gradio(), path="/ui/gradio", css=CSS, ssr_mode=False, footer_links=[])
    UIS["gradio"] = "/ui/gradio/"
