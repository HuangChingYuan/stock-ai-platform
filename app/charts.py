"""Plotly 圖表，Gradio 與 Dash 共用。台股配色：紅漲綠跌。"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

UP, DOWN, INK, MUTED = "#d23b3b", "#1f8a5b", "#16202b", "#8a96a3"


def _empty(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(size=16, color=MUTED))
    fig.update_layout(xaxis_visible=False, yaxis_visible=False, template="plotly_white", height=420)
    return fig


def kline(prices: pd.DataFrame, ind: pd.DataFrame, title: str = "") -> go.Figure:
    """title 保留給外部使用；圖內不顯示標題，避免手機上與圖例重疊。"""
    if prices.empty:
        return _empty("尚無股價資料，請先執行每日排程")
    df = prices.merge(ind, on="date", how="left") if not ind.empty else prices
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.6, 0.18, 0.22], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=df["date"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                                 increasing_line_color=UP, increasing_fillcolor=UP,
                                 decreasing_line_color=DOWN, decreasing_fillcolor=DOWN, name="K 線"), 1, 1)
    for col, color in (("ma5", "#e0a100"), ("ma20", "#1d5fa8"), ("ma60", "#7b4bb3")):
        if col in df:
            fig.add_trace(go.Scatter(x=df["date"], y=df[col], name=col.upper(), line=dict(width=1.3, color=color)), 1, 1)
    if "bb_upper" in df:
        for col in ("bb_upper", "bb_lower"):
            fig.add_trace(go.Scatter(x=df["date"], y=df[col], name="布林", showlegend=col == "bb_upper",
                                     line=dict(width=1, dash="dot", color=MUTED)), 1, 1)
    colors = [UP if c >= o else DOWN for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=df["date"], y=df["volume"], marker_color=colors, name="成交量", showlegend=False), 2, 1)
    if "k" in df:
        fig.add_trace(go.Scatter(x=df["date"], y=df["k"], name="K", line=dict(width=1.3, color=UP)), 3, 1)
        fig.add_trace(go.Scatter(x=df["date"], y=df["d"], name="D", line=dict(width=1.3, color="#1d5fa8")), 3, 1)
        for lvl in (20, 80):
            fig.add_hline(y=lvl, line=dict(width=0.8, dash="dash", color=MUTED), row=3, col=1)
    fig.update_layout(template="plotly_white", height=640,
                      margin=dict(l=40, r=20, t=40, b=20), xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
                      font=dict(family="Noto Sans TC, sans-serif", color=INK))
    # 跳過週末與國定假日（春節、清明等沒有交易資料的日子）
    dates = pd.to_datetime(df["date"])
    holidays = pd.bdate_range(dates.min(), dates.max()).difference(dates)
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"]),
                                  dict(values=holidays.strftime("%Y-%m-%d").tolist())])
    fig.update_yaxes(range=[0, 100], row=3, col=1)   # KD 固定 0–100
    fig.update_layout(hovermode="x unified")         # 滑過時同一天的價、量、KD 一起顯示
    return fig


def revenue(rev: pd.DataFrame, title: str = "") -> go.Figure:
    if rev.empty:
        return _empty("尚無月營收資料")
    label = [f"{y}/{m:02d}" for y, m in zip(rev["year"], rev["month"])]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=label, y=rev["revenue"] / 1e8, name="營收（億元）", marker_color="#1d5fa8"), secondary_y=False)
    fig.add_trace(go.Scatter(x=label, y=rev["yoy"], name="年增率 %", mode="lines+markers",
                             line=dict(color="#e0a100", width=2)), secondary_y=True)
    fig.update_layout(template="plotly_white", height=460, margin=dict(l=40, r=40, t=40, b=40),
                      legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0), font=dict(family="Noto Sans TC, sans-serif", color=INK))
    fig.update_yaxes(title_text="億元", secondary_y=False)
    fig.update_yaxes(title_text="年增率 %", secondary_y=True)
    return fig
