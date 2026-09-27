"""Streamlit UI：自選股總覽與訊號篩選。部署在 Streamlit Community Cloud（免費）。

- 直接連 Neon PostgreSQL（在 Streamlit Cloud 的 Secrets 設定 DATABASE_URL），不經過 Render，
  所以 Render 休眠時這一頁仍可使用。
- PWA 外殼以 iframe 嵌入：https://<app>.streamlit.app/?embed=true&stock=2330
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 讓 Streamlit Cloud 找得到 core/

# Streamlit Cloud 的 Secrets 轉成環境變數，core.config 才讀得到
for key in ("DATABASE_URL", "WATCHLIST"):
    try:
        if key in st.secrets and key not in os.environ:
            os.environ[key] = str(st.secrets[key])
    except FileNotFoundError:  # 本機沒有 secrets.toml
        pass

from core import indicators as ta  # noqa: E402
from core import repository as repo  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.db import session_scope  # noqa: E402

st.set_page_config(page_title="自選股總覽", layout="wide")


@st.cache_data(ttl=600)
def load_overview(ids: tuple[str, ...]) -> pd.DataFrame:
    rows = []
    with session_scope() as s:
        for sid in ids:
            snap = repo.snapshot(s, sid)
            ind = repo.indicators_df(s, sid, 2)
            prices = repo.prices_df(s, sid, 2)
            rep = repo.latest_report(s, sid)
            last = ind.iloc[-1] if not ind.empty else {}
            rows.append({
                "代號": sid, "名稱": snap["name"], "收盤": snap["close"], "漲跌%": snap["change_pct"],
                "RSI": last.get("rsi14") if len(ind) else None, "K": last.get("k") if len(ind) else None,
                "訊號": "；".join(ta.signals(ind, prices)),
                "AI 建議": rep.action if rep else "", "信心": rep.confidence if rep else None,
            })
    return pd.DataFrame(rows)


with session_scope() as s:
    ids = tuple(dict.fromkeys(get_settings().watchlist + repo.watched_stock_ids(s)))

df = load_overview(ids)
focus = st.query_params.get("stock", "").upper()

st.subheader("自選股總覽")
c1, c2, c3 = st.columns(3)
action = c1.multiselect("AI 建議", ["買進", "觀望", "賣出"], default=[])
rsi_max = c2.slider("RSI 上限", 0, 100, 100)
kw = c3.text_input("訊號關鍵字", placeholder="例如：黃金交叉")

view = df.copy()
if action:
    view = view[view["AI 建議"].isin(action)]
view = view[view["RSI"].fillna(0) <= rsi_max]
if kw:
    view = view[view["訊號"].str.contains(kw, na=False)]


def color_change(v):
    if pd.isna(v) or v == 0:
        return ""
    return "color:#d23b3b" if v > 0 else "color:#1f8a5b"  # 台股：紅漲綠跌


def highlight(row):
    return ["background-color:#eaf1fb" if row["代號"] == focus else "" for _ in row]


st.dataframe(
    view.style.apply(highlight, axis=1).map(color_change, subset=["漲跌%"])
        .format({"收盤": "{:g}", "漲跌%": "{:+.2f}", "RSI": "{:.0f}", "K": "{:.0f}", "信心": "{:.0f}"}, na_rep="–"),
    hide_index=True, width="stretch",
)
st.caption(f"共 {len(view)} / {len(df)} 檔　資料每 10 分鐘更新快取　僅供學習研究，不構成投資建議")
