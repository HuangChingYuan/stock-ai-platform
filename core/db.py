"""資料庫連線。正式環境用 Neon PostgreSQL，本機未設定 DATABASE_URL 時退回 SQLite。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_url(url: str) -> str:
    """Neon 給的是 postgresql://，SQLAlchemy 需指定 psycopg (v3) 驅動。"""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _make_engine():
    url = normalize_url(get_settings().database_url)
    if url in ("sqlite://", "sqlite:///:memory:"):  # 記憶體資料庫（測試用）：各執行緒共用同一個連線
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    # Neon 閒置 5 分鐘會暫停 compute：pre_ping 讓斷線連線自動重建；連線數保持很小。
    return create_engine(url, pool_pre_ping=True, pool_recycle=240, pool_size=3, max_overflow=2)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from core import models  # noqa: F401  註冊資料表

    Base.metadata.create_all(engine)
