"""回測：把歷史報告和之後的股價比對，算出 AI 建議與規則式判斷的勝率。

勝負定義（不計手續費、證交稅與滑價）：
- 買進：N 個交易日後的收盤價高於報告當天收盤價算贏
- 賣出：N 個交易日後的收盤價低於報告當天收盤價算贏
- 觀望：不計入勝率，只計次數
對照組「每天都買」是同一批日子 N 日後上漲的比例：LLM 和規則至少要贏過它才算有判斷力。
預設只比較有 LLM 報告的日子（同日比較），避免兩者因樣本期間不同而失真。
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import indicators as ta
from core import repository as repo
from core.models import Report

HORIZONS = (5, 20)
SOURCES = {"llm": "LLM", "rules": "規則式", "always_buy": "每天都買"}
COLUMNS = ["來源", "樣本", "買進", "賣出", "觀望", "勝率%", "買進勝率%", "賣出勝率%", "平均報酬%"]


def signals_frame(session: Session, stock_ids: list[str] | None = None,
                  horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    """每份報告展開成 LLM 與規則式各一列，附上 N 日後報酬（ret_N，尚未滿 N 日為 NaN）。"""
    stmt = select(Report).order_by(Report.stock_id, Report.date)
    if stock_ids:
        stmt = stmt.where(Report.stock_id.in_(stock_ids))
    by_stock: dict[str, list[Report]] = {}
    for r in session.scalars(stmt):
        by_stock.setdefault(r.stock_id, []).append(r)

    rows = []
    for sid, reports in by_stock.items():
        prices = repo.prices_df(session, sid, limit=5000)
        if prices.empty:
            continue
        ind = None
        for r in reports:
            i = int(prices["date"].searchsorted(r.date, side="right")) - 1  # 報告當天（或之前最近）的收盤
            if i < 0:
                continue
            base = {"stock_id": sid, "date": r.date, "provider": r.provider,
                    **{f"ret_{h}": _forward_return(prices, i, h) for h in horizons}}
            if r.provider != "rules":
                rows.append({**base, "source": "llm", "action": r.action, "confidence": r.confidence})
            if r.provider == "rules":
                rule = (r.action, r.confidence)
            elif r.rule_action:
                rule = (r.rule_action, r.rule_confidence)
            else:  # 舊報告沒有存規則式判斷：用當天以前的指標重算
                if ind is None:
                    ind = repo.indicators_df(session, sid, limit=5000)
                rule = _rule_at(ind, prices, r.date)
            rows.append({**base, "source": "rules", "action": rule[0], "confidence": rule[1]})
    cols = ["stock_id", "date", "source", "provider", "action", "confidence", *[f"ret_{h}" for h in horizons]]
    return pd.DataFrame(rows, columns=cols)


def _forward_return(prices: pd.DataFrame, i: int, h: int) -> float:
    if i + h >= len(prices):
        return float("nan")
    now, later = prices.iloc[i]["close"], prices.iloc[i + h]["close"]
    return float(later) / float(now) - 1 if now else float("nan")


def _rule_at(ind: pd.DataFrame, prices: pd.DataFrame, day) -> tuple[str, int]:
    action, conf, _ = ta.rule_score(ind[ind["date"] <= day] if not ind.empty else ind, prices[prices["date"] <= day])
    return action, conf


def summarize(frame: pd.DataFrame, horizon: int, paired: bool = True) -> pd.DataFrame:
    """依來源彙總某個持有天數的勝率。paired=True 時只看有 LLM 報告的日子。"""
    ret = f"ret_{horizon}"
    df = frame.dropna(subset=[ret])
    if paired and (df["source"] == "llm").any():
        days = df.loc[df["source"] == "llm", ["stock_id", "date"]]
        df = df.merge(days, on=["stock_id", "date"])
    rules = df[df["source"] == "rules"]  # 每份報告都有一列規則式，用來當「每天都買」的樣本日
    always = rules.assign(source="always_buy", action="買進")

    out = []
    for key, g in pd.concat([df, always]).groupby("source", sort=False):
        buy, sell = g[g["action"] == "買進"], g[g["action"] == "賣出"]
        directional = pd.concat([buy[ret], -sell[ret]])
        out.append({
            "來源": SOURCES.get(key, key), "樣本": len(g), "買進": len(buy), "賣出": len(sell),
            "觀望": len(g) - len(buy) - len(sell),
            "勝率%": _pct((directional > 0).mean()) if len(directional) else None,
            "買進勝率%": _pct((buy[ret] > 0).mean()) if len(buy) else None,
            "賣出勝率%": _pct((sell[ret] < 0).mean()) if len(sell) else None,
            "平均報酬%": _pct(directional.mean()) if len(directional) else None,
        })
    if not out:
        return pd.DataFrame(columns=COLUMNS)
    order = list(SOURCES.values())
    return pd.DataFrame(out, columns=COLUMNS).sort_values("來源", key=lambda s: s.map(order.index)).reset_index(drop=True)


def _pct(v: float) -> float:
    return round(float(v) * 100, 1)
