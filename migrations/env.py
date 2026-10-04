"""Alembic 執行環境：沿用 core.db 的連線設定（DATABASE_URL），不另外設定網址。"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from core import models  # noqa: F401  註冊資料表
from core.db import Base, engine


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite 改欄位需要重建資料表
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")  # core.db.init_db 會傳入
if connection is not None:
    _run(connection)
else:  # 從命令列執行 alembic
    if context.config.config_file_name:
        fileConfig(context.config.config_file_name, disable_existing_loggers=False)
    with engine.begin() as conn:
        _run(conn)
