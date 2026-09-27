"""Dash UI：月營收與基本面。以 WSGI 掛載於 FastAPI 的 /ui/dash/。

網址參數：?stock=2330
"""
from __future__ import annotations

from urllib.parse import parse_qs

import dash
from dash import Input, Output, dash_table, dcc, html

from app import charts
from core import repository as repo
from core.config import get_settings
from core.db import session_scope

FONT = "'Noto Sans TC', system-ui, sans-serif"


def build(prefix: str = "/ui/dash/") -> dash.Dash:
    app = dash.Dash(__name__, requests_pathname_prefix=prefix, routes_pathname_prefix="/", title="月營收")
    watch = get_settings().watchlist

    app.layout = html.Div(style={"fontFamily": FONT, "padding": "16px", "color": "#16202b"}, children=[
        dcc.Location(id="url", refresh=False),
        html.Div(style={"display": "flex", "gap": "12px", "alignItems": "center", "flexWrap": "wrap"}, children=[
            html.Label("股票代號", htmlFor="stock"),
            dcc.Input(id="stock", type="text", value=watch[0] if watch else "", debounce=True,
                      style={"width": "120px", "padding": "6px 10px", "fontSize": "16px"}),
            html.Span(id="headline", style={"fontWeight": 700, "fontSize": "18px"}),
        ]),
        dcc.Graph(id="rev-chart", config={"displaylogo": False, "responsive": True}),
        dash_table.DataTable(
            id="rev-table",
            columns=[{"name": n, "id": i} for n, i in
                     (("年", "year"), ("月", "month"), ("營收（千元）", "revenue_k"), ("年增率 %", "yoy"))],
            style_cell={"fontFamily": FONT, "padding": "6px 10px", "textAlign": "right"},
            style_header={"fontWeight": 700, "backgroundColor": "#eef1f4"},
            page_size=12,
        ),
    ])

    @app.callback(Output("stock", "value"), Input("url", "search"))
    def from_url(search):
        sid = parse_qs((search or "").lstrip("?")).get("stock", [None])[0]
        return (sid or (watch[0] if watch else "")).upper()

    @app.callback(Output("rev-chart", "figure"), Output("rev-table", "data"), Output("headline", "children"),
                  Input("stock", "value"))
    def update(stock_id):
        stock_id = (stock_id or "").strip().upper()
        with session_scope() as s:
            rev = repo.revenue_df(s, stock_id, 36)
            name = repo.stock_name(s, stock_id)
        rows = [] if rev.empty else [
            {"year": r.year, "month": r.month, "revenue_k": f"{r.revenue / 1000:,.0f}",
             "yoy": "" if r.yoy is None or r.yoy != r.yoy else f"{r.yoy:+.1f}"}
            for r in rev.iloc[::-1].itertuples()
        ]
        return charts.revenue(rev, "近 36 個月營收"), rows, f"{stock_id} {name}"

    return app
