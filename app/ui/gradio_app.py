"""Gradio UI：個股 K 線、技術指標與 AI 報告。掛載於 FastAPI 的 /ui/gradio。

網址參數（PWA 外殼用 iframe 帶入）：
  ?stock=2330            預設顯示的股票
  ?view=chart|report     預設開啟的分頁
"""
from __future__ import annotations

import gradio as gr

from app import charts
from core import indicators as ta
from core import report as rpt
from core import repository as repo
from core.config import get_settings
from core.db import session_scope


def render(stock_id: str):
    stock_id = (stock_id or "").strip().upper()
    if not stock_id:
        return charts._empty("請輸入股票代號"), "", ""
    with session_scope() as s:
        prices = repo.prices_df(s, stock_id, 180)
        ind = repo.indicators_df(s, stock_id, 180)
        name = repo.stock_name(s, stock_id)
        sig = ta.signals(ind, prices)
        md = rpt.to_markdown(repo.latest_report(s, stock_id), name)
    fig = charts.kline(prices, ind, f"{stock_id} {name}")
    sig_md = "**今日訊號**　" + ("；".join(sig) if sig else "沒有明顯訊號")
    return fig, sig_md, md


def regenerate(stock_id: str):
    stock_id = (stock_id or "").strip().upper()
    with session_scope() as s:
        data = rpt.generate(s, stock_id)
        name = repo.stock_name(s, stock_id)
    return rpt.to_markdown(data, name)


def build() -> gr.Blocks:
    watch = get_settings().watchlist
    with gr.Blocks(title="個股分析", fill_width=True, analytics_enabled=False) as demo:
        with gr.Row():
            stock = gr.Dropdown(choices=watch, value=watch[0] if watch else None, allow_custom_value=True,
                                label="股票代號", scale=1)
            signal_md = gr.Markdown(scale=3)
        with gr.Tabs() as tabs:
            with gr.Tab("K 線與指標", id="chart"):
                plot = gr.Plot(show_label=False)
            with gr.Tab("AI 報告", id="report"):
                report_md = gr.Markdown()
                regen = gr.Button("重新產生報告", variant="secondary")
                gr.Markdown("重新產生會呼叫 LLM 免費額度，請勿連續點擊。")

        def on_load(request: gr.Request):
            q = request.query_params
            sid = (q.get("stock") or (watch[0] if watch else "")).upper()
            view = q.get("view") if q.get("view") in {"chart", "report"} else "chart"
            fig, sig_md, md = render(sid)
            return gr.update(value=sid), fig, sig_md, md, gr.Tabs(selected=view)

        demo.load(on_load, None, [stock, plot, signal_md, report_md, tabs])
        stock.input(render, stock, [plot, signal_md, report_md])
        regen.click(regenerate, stock, report_md, concurrency_limit=1)
    return demo


CSS = """
.gradio-container { font-family: 'Noto Sans TC', system-ui, sans-serif; }
footer { display: none !important; }
"""
