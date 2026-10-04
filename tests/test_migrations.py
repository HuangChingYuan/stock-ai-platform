from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from core import models  # noqa: F401
from core.db import ROOT, Base, engine, init_db


def _reset():
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


def _head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _diff():
    with engine.connect() as conn:
        return compare_metadata(MigrationContext.configure(conn), Base.metadata)


def test_fresh_database_matches_models():
    """改了 core/models.py 卻忘了產生 migration，這個測試會失敗。"""
    _reset()
    init_db()
    assert _diff() == []
    init_db()  # 再跑一次不做任何事


def test_existing_create_all_database_is_stamped_not_rebuilt():
    _reset()
    Base.metadata.create_all(engine)  # 導入 Alembic 前的建立方式
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO stocks (stock_id, name) VALUES ('2330', '台積電')"))
    init_db()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == _head()
        assert conn.execute(text("SELECT name FROM stocks")).scalar() == "台積電"
    assert "alembic_version" in inspect(engine).get_table_names()
    _reset()
