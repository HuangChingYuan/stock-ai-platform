"""建議買賣報告：整理資料 → 呼叫 LLM → 驗證 → 寫入 reports 資料表。"""
from __future__ import annotations

import json

import pandas as pd
from sqlalchemy.orm import Session

from core import indicators as ta
from core import llm, repository as repo
from core.models import Report

SYSTEM_PROMPT = """你是台股研究助理。只能根據使用者提供的資料分析，不可引用資料以外的數字或事件。
輸出必須是單一 JSON 物件，不要任何其他文字，格式：
{"action": "買進|觀望|賣出", "confidence": 0-100 的整數,
 "summary": "兩句以內的結論",
 "reasons": ["理由（需引用提供的數據）", ...最多 4 點],
 "risks": ["風險提示", ...最多 3 點]}
資料不足時 action 請給「觀望」並降低 confidence。這份報告僅供學習研究，不是投資建議。"""

ACTIONS = {"買進", "觀望", "賣出"}


def build_context(session: Session, stock_id: str) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    prices = repo.prices_df(session, stock_id, limit=60)
    ind = repo.indicators_df(session, stock_id, limit=60)
    rev = repo.revenue_df(session, stock_id, limit=18)  # 最近 6 個月都要有去年同月才算得出年增率
    news = repo.latest_news(session, stock_id, limit=8)

    lines = [f"股票：{stock_id} {repo.stock_name(session, stock_id)}"]
    if not prices.empty:
        p = prices.tail(20)
        lines.append("近 20 日收盤：" + ", ".join(f"{d:%m/%d} {c:g}" for d, c in zip(p["date"], p["close"])))
        lines.append(f"近 20 日均量：{p['volume'].mean():,.0f} 股")
    if not ind.empty:
        last = ind.iloc[-1]
        lines.append("最新指標：" + ", ".join(
            f"{k}={last[k]:.2f}" for k in ("ma5", "ma20", "ma60", "rsi14", "k", "d", "macd_hist") if pd.notna(last[k])))
        lines.append("技術訊號：" + ("；".join(ta.signals(ind, prices)) or "無明顯訊號"))
    if not rev.empty:
        lines.append("月營收（年/月：營收 千元, 年增%）：" + "; ".join(
            f"{r.year}/{r.month}: {r.revenue / 1000:,.0f}" + (f", {r.yoy:+.1f}%" if pd.notna(r.yoy) else "")
            for r in rev.tail(6).itertuples()))
    if news:
        lines.append("近期新聞標題：\n" + "\n".join(f"- {n.date:%m/%d} {n.title}（{n.source}）" for n in news if n.date))
    return "\n".join(lines), prices, ind


def _normalize(data: dict) -> dict:
    action = data.get("action") if data.get("action") in ACTIONS else "觀望"
    try:
        conf = max(0, min(100, int(data.get("confidence", 50))))
    except (TypeError, ValueError):
        conf = 50
    as_list = lambda v: [str(x) for x in v][:4] if isinstance(v, list) else ([str(v)] if v else [])
    return {"action": action, "confidence": conf, "summary": str(data.get("summary", ""))[:500],
            "reasons": as_list(data.get("reasons")), "risks": as_list(data.get("risks"))[:3]}


def generate(session: Session, stock_id: str, use_llm: bool = True) -> dict:
    context, prices, ind = build_context(session, stock_id)
    result = llm.chat_json(SYSTEM_PROMPT, context) if use_llm else None

    if result:
        data, provider, model = result
        data = _normalize(data)
    else:
        action, conf, sig = ta.rule_score(ind, prices)
        data = {"action": action, "confidence": conf,
                "summary": "未使用 LLM，依技術指標規則判斷。",
                "reasons": sig or ["資料不足"], "risks": ["僅依技術面判斷，未納入基本面與消息面"]}
        provider, model = "rules", "rule_score"

    day = prices.iloc[-1]["date"] if not prices.empty else repo.today()
    repo.upsert(session, Report, [{
        "stock_id": stock_id, "date": day, "action": data["action"], "confidence": data["confidence"],
        "summary": data["summary"], "reasons": "\n".join(data["reasons"]), "risks": "\n".join(data["risks"]),
        "provider": provider, "model": model,
    }], keys=["stock_id", "date"])
    return {**data, "stock_id": stock_id, "date": str(day), "provider": provider, "model": model}


def to_markdown(r: Report | dict | None, name: str = "") -> str:
    if r is None:
        return "尚無報告。排程每個交易日盤後產生，也可以按「重新產生報告」。"
    get = (lambda k: r.get(k)) if isinstance(r, dict) else (lambda k: getattr(r, k))
    reasons = get("reasons"); risks = get("risks")
    reasons = reasons.split("\n") if isinstance(reasons, str) else reasons
    risks = risks.split("\n") if isinstance(risks, str) else risks
    return "\n".join([
        f"### {get('stock_id')} {name}　{get('action')}（信心 {get('confidence')}）",
        f"{get('date')}　由 {get('provider')} / {get('model')} 產生", "",
        get("summary") or "", "", "**理由**", *[f"- {x}" for x in reasons if x], "",
        "**風險**", *[f"- {x}" for x in risks if x], "",
        "> 僅供學習研究，不構成投資建議。",
    ])


def to_json(r: Report) -> str:
    return json.dumps({c.name: str(getattr(r, c.name)) for c in Report.__table__.columns}, ensure_ascii=False)
