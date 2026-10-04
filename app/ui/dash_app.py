"""Dash UI：月營收與基本面。以 WSGI 掛載於 FastAPI 的 /ui/dash/。

網址參數：?stock=2330
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qs

import dash
from dash import Input, Output, dash_table, dcc, html

from app import charts
from core import repository as repo
from core.config import get_settings
from core.db import session_scope

log = logging.getLogger(__name__)
FONT = "'Noto Sans TC', system-ui, sans-serif"
WARN = {"color": "#d23b3b", "fontWeight": 500, "fontSize": "15px"}


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
        html.P(id="notice", role="status", style=WARN),
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
                  Output("notice", "children"), Input("stock", "value"))
    def update(stock_id):
        # 正式環境 Dash 的 callback 例外只會讓畫面停在舊內容，所以錯誤都轉成畫面上的說明
        stock_id = (stock_id or "").strip().upper()
        if not stock_id:
            return charts._empty("請輸入股票代號"), [], "", "請在上方輸入股票代號，例如 2330"
        if not repo.valid_stock_id(stock_id):
            return charts._empty("不是有效的股票代號"), [], "", f"「{stock_id}」不是有效的股票代號，請輸入 4 到 6 碼數字"
        try:
            with session_scope() as s:
                rev = repo.revenue_df(s, stock_id, 36)
                name = repo.stock_name(s, stock_id)
        except Exception:
            log.exception("Dash 讀取 %s 月營收失敗", stock_id)
            msg = "資料庫暫時無法連線，請稍後重新整理"
            return charts._empty(msg), [], stock_id, msg
        rows = [] if rev.empty else [
            {"year": r.year, "month": r.month, "revenue_k": f"{r.revenue / 1000:,.0f}",
             "yoy": "" if r.yoy is None or r.yoy != r.yoy else f"{r.yoy:+.1f}"}
            for r in rev.iloc[::-1].itertuples()
        ]
        notice = "" if rows else f"資料庫裡還沒有 {stock_id} 的月營收（ETF 沒有月營收；個股可等每日排程下載）"
        return charts.revenue(rev, "近 36 個月營收"), rows, f"{stock_id} {name}", notice

    return app
