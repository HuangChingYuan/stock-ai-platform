"""Gradio UI：個股 K 線、技術指標與 AI 報告。掛載於 FastAPI 的 /ui/gradio。

網址參數（PWA 外殼用 iframe 帶入）：
  ?stock=2330            預設顯示的股票
  ?view=chart|report     預設開啟的分頁
"""
from __future__ import annotations

import logging

import gradio as gr

from app import charts
from core import indicators as ta
from core import report as rpt
from core import repository as repo
from core.config import get_settings
from core.db import session_scope

log = logging.getLogger(__name__)
PERIODS = {"3 個月": 66, "6 個月": 130, "1 年": 250}
REGEN_LABEL = "重新產生報告"


def render(stock_id: str, period: str = "6 個月"):
    days = PERIODS.get(period, 130)
    stock_id = (stock_id or "").strip().upper()
    if not stock_id:
        return charts._empty("請輸入股票代號"), "請先在左上角輸入股票代號，例如 2330", ""
    if not repo.valid_stock_id(stock_id):
        return charts._empty(f"「{stock_id}」不是有效的股票代號"), f"⚠️「{stock_id}」不是有效的股票代號，請輸入 4 到 6 碼數字，例如 2330", ""
    try:
        with session_scope() as s:
            prices = repo.prices_df(s, stock_id, days)
            ind = repo.indicators_df(s, stock_id, days)
            name = repo.stock_name(s, stock_id)
            sig = ta.signals(ind, prices)
            md = rpt.to_markdown(repo.latest_report(s, stock_id), name, with_context=True)
    except Exception:  # 資料庫暫停或連線中斷：畫面要說明原因，不能只留下空白圖
        log.exception("Gradio 讀取 %s 失敗", stock_id)
        msg = "資料庫暫時無法連線，請稍後重新整理"
        return charts._empty(msg), f"⚠️ {msg}", f"⚠️ {msg}"
    if prices.empty:  # 沒有股價時「沒有明顯訊號」會誤導，直接說明查無資料
        return (charts.kline(prices, ind), f"⚠️ 資料庫裡還沒有 {stock_id} 的股價。"
                "在 App 上方輸入代號按「查看」會立即下載，或等每日排程。", md)
    fig = charts.kline(prices, ind, f"{stock_id} {name}")
    sig_md = "**今日訊號**　" + ("；".join(sig) if sig else "沒有明顯訊號")
    return fig, sig_md, md


def regenerate(stock_id: str):
    sid = (stock_id or "").strip().upper()
    try:
        return rpt.regenerate_markdown(sid)
    except Exception:  # LLM 全部失敗會改用規則判斷；走到這裡多半是資料庫錯誤
        log.exception("重新產生 %s 報告失敗", sid)
        return f"⚠️ {sid} 報告產生失敗，資料庫或服務暫時無法使用，請稍後再試。"


def build() -> gr.Blocks:
    watch = get_settings().watchlist
    with gr.Blocks(title="個股分析", fill_width=True, analytics_enabled=False) as demo:
        with gr.Row():
            stock = gr.Dropdown(choices=watch, value=watch[0] if watch else None, allow_custom_value=True,
                                label="股票代號", scale=1)
            period = gr.Radio(list(PERIODS), value="6 個月", label="期間", scale=1)
            signal_md = gr.Markdown(scale=3)
        with gr.Tabs() as tabs:
            with gr.Tab("K 線與指標", id="chart"):
                plot = gr.Plot(show_label=False)
            with gr.Tab("AI 報告", id="report"):
                report_md = gr.Markdown()
                regen = gr.Button(REGEN_LABEL, variant="secondary")
                gr.Markdown("重新產生會呼叫 LLM 免費額度，請勿連續點擊。")

        def on_load(request: gr.Request):
            q = request.query_params
            sid = (q.get("stock") or (watch[0] if watch else "")).upper()
            view = q.get("view") if q.get("view") in {"chart", "report"} else "chart"
            fig, sig_md, md = render(sid)
            return gr.update(value=sid), fig, sig_md, md, gr.Tabs(selected=view)

        demo.load(on_load, None, [stock, plot, signal_md, report_md, tabs])
        stock.input(render, [stock, period], [plot, signal_md, report_md])
        period.change(render, [stock, period], [plot, signal_md, report_md])
        # 產生期間停用按鈕並改字（LLM 可能要數十秒），避免以為沒反應而連點
        regen.click(lambda: gr.Button(value="產生中，約需 30 秒…", interactive=False), None, regen, queue=False) \
            .then(regenerate, stock, report_md, concurrency_limit=1) \
            .then(lambda: gr.Button(value=REGEN_LABEL, interactive=True), None, regen, queue=False)
    return demo


CSS = """
.gradio-container { font-family: 'Noto Sans TC', system-ui, sans-serif; }
footer { display: none !important; }
"""
