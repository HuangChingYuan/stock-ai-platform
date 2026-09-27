"""Telegram 機器人 webhook。指令：/price /signal /report /watch /unwatch /list /help"""
from __future__ import annotations

import re

from fastapi import APIRouter, Header, HTTPException, Request

from core import indicators as ta
from core import report as rpt
from core import repository as repo
from core.config import get_settings
from core.db import session_scope
from core.models import Watch
from core.notify import send_telegram

router = APIRouter(tags=["telegram"])

HELP = """台股 AI 小幫手
/price 2330　最新收盤
/signal 2330　技術指標訊號
/report 2330　AI 建議報告
/watch 2330　加入自選股（盤後推播）
/unwatch 2330　移除自選股
/list　我的自選股
報告僅供學習研究，不構成投資建議。"""


def handle(chat_id: str, text: str) -> str:
    m = re.match(r"^/(\w+)(?:@\w+)?\s*(\S+)?", text.strip())
    if not m:
        return HELP
    cmd, arg = m.group(1).lower(), (m.group(2) or "").upper()
    if cmd in {"start", "help"}:
        return HELP
    with session_scope() as s:
        if cmd == "list":
            ids = [w.stock_id for w in s.query(Watch).filter_by(chat_id=chat_id)]
            return "自選股：" + ("、".join(ids) if ids else "尚未加入，使用 /watch 2330")
        if not arg:
            return f"請加上股票代號，例如 /{cmd} 2330"
        if cmd == "watch":
            repo.upsert(s, Watch, [{"chat_id": chat_id, "stock_id": arg}], keys=["chat_id", "stock_id"])
            return f"已加入 {arg}，之後每個交易日盤後推播訊號與報告。資料會在下次排程時開始抓取。"
        if cmd == "unwatch":
            s.query(Watch).filter_by(chat_id=chat_id, stock_id=arg).delete()
            return f"已移除 {arg}"
        if cmd == "price":
            snap = repo.snapshot(s, arg)
            if snap["close"] is None:
                return f"資料庫裡還沒有 {arg} 的股價。"
            chg = f"{snap['change']:+g}（{snap['change_pct']:+.2f}%）" if snap["change"] is not None else ""
            return f"{arg} {snap['name']}\n{snap['date']} 收盤 {snap['close']:g} {chg}\n成交量 {snap['volume']:,} 股"
        if cmd == "signal":
            sig = ta.signals(repo.indicators_df(s, arg, 5), repo.prices_df(s, arg, 5))
            return f"{arg} 技術訊號\n" + ("\n".join(f"・{x}" for x in sig) if sig else "目前沒有明顯訊號")
        if cmd == "report":
            r = repo.latest_report(s, arg)
            if r is None:
                return f"{arg} 還沒有報告，排程會在盤後產生。"
            text = rpt.to_markdown(r, repo.stock_name(s, arg)).replace("### ", "").replace("**", "")
            base = get_settings().public_base_url
            return text + (f"\n\n完整圖表：{base}/ui/gradio/?stock={arg}&view=report" if base else "")
    return HELP


@router.post("/telegram/webhook")
async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    secret = get_settings().telegram_webhook_secret
    if secret and x_telegram_bot_api_secret_token != secret:
        raise HTTPException(403, "invalid secret")
    update = await request.json()
    msg = update.get("message") or update.get("edited_message") or {}
    text, chat = msg.get("text"), msg.get("chat", {}).get("id")
    if text and chat:
        from starlette.concurrency import run_in_threadpool

        reply = await run_in_threadpool(handle, str(chat), text)
        await run_in_threadpool(send_telegram, chat, reply)
    return {"ok": True}
