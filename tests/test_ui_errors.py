"""Gradio／Dash 讀取失敗時要在畫面上說明原因，不能留下空白或沒反應。"""
import pytest

pytest.importorskip("gradio")  # 排程的 requirements-jobs.txt 不含 UI 套件


def _boom(*_a, **_k):
    raise RuntimeError("db down")


def test_gradio_render_explains_errors(db, monkeypatch):
    from app.ui import gradio_app

    _, sig, _ = gradio_app.render("ABC")
    assert "有效" in sig
    _, sig, _ = gradio_app.render("2330")  # 資料庫是空的
    assert "還沒有 2330 的股價" in sig
    monkeypatch.setattr(gradio_app.repo, "prices_df", _boom)
    fig, sig, md = gradio_app.render("2330")
    assert "資料庫暫時無法連線" in sig and "資料庫暫時無法連線" in md


def test_gradio_regenerate_reports_failure(db, monkeypatch):
    from app.ui import gradio_app

    monkeypatch.setattr(gradio_app.rpt, "regenerate_markdown", _boom)
    assert "失敗" in gradio_app.regenerate("2330")


def test_dash_update_explains_errors(db, monkeypatch):
    from app.ui import dash_app

    app = dash_app.build()
    update = app.callback_map["..rev-chart.figure...rev-table.data...headline.children...notice.children.."]["callback"]
    call = lambda sid: update(sid, outputs_list=[{"id": i, "property": p} for i, p in (
        ("rev-chart", "figure"), ("rev-table", "data"), ("headline", "children"), ("notice", "children"))])
    assert "有效" in call("ABC")
    assert "還沒有 2330 的月營收" in call("2330")
    monkeypatch.setattr(dash_app.repo, "revenue_df", _boom)
    assert "資料庫暫時無法連線" in call("2330")
