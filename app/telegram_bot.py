"""Telegram 機器人 webhook。直接輸入代號或名稱看速覽；指令：/report /watch /unwatch /list /price /signal /help"""
from __future__ import annotations

import hmac
import logging
import re

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from core import data_sources as ds
from core import indicators as ta
from core import report as rpt
from core import repository as repo
from core.config import get_settings
from core.db import session_scope
from core.models import Watch
from core.notify import send_telegram

log = logging.getLogger(__name__)
router = APIRouter(tags=["telegram"])

HELP = """台股 AI 小幫手
直接輸入代號或名稱（例如 2330、台積電）：報價、訊號與 AI 建議
/report 2330　完整 AI 報告（理由與風險）
/watch 2330　加入自選股（立刻下載資料，之後盤後推播）
/unwatch 2330　移除自選股
/list　我的自選股與最新收盤
報告僅供學習研究，不構成投資建議。"""

# Telegram 輸入框的指令選單（啟動時由 sync_webhook 設定）
COMMANDS = [
    ("report", "完整 AI 報告，例如 /report 2330"),
    ("watch", "加入自選股，例如 /watch 2330"),
    ("unwatch", "移除自選股"),
    ("list", "我的自選股與最新收盤"),
    ("help", "使用說明"),
]


def _resolve(s, arg: str) -> tuple[str | None, str]:
    """代號或名稱 → (代號, '')；找不到或有多檔時回傳 (None, 提示訊息)。"""
    matches = repo.find_stocks(s, arg)
    if len(matches) == 1:
        return matches[0][0], ""
    if matches:
        return None, f"符合「{arg[:20]}」的股票有好幾檔，請輸入代號：\n" + "\n".join(f"{sid} {name}" for sid, name in matches)
    if re.fullmatch(r"[0-9A-Za-z]+", arg):
        return None, f"「{arg[:20]}」不是有效的股票代號，例如 2330、0050、00631L"
    return None, f"找不到名稱含「{arg[:20]}」的股票，請改輸入代號，例如 2330（/help 看用法）"


def _quote(s, sid: str) -> str:
    """一則訊息看完：收盤、技術訊號、最新 AI 建議。"""
    snap = repo.snapshot(s, sid)
    if snap["close"] is None:
        return f"資料庫裡還沒有 {sid} {snap['name']} 的股價。用 /watch {sid} 加入自選股會立刻下載。"
    chg = f"{snap['change']:+g}（{snap['change_pct']:+.2f}%）" if snap["change"] is not None else ""
    lines = [f"{sid} {snap['name']}", f"{snap['date']} 收盤 {snap['close']:g} {chg}".rstrip()]
    sig = ta.signals(repo.indicators_df(s, sid, 5), repo.prices_df(s, sid, 5))
    lines.append("訊號：" + ("；".join(sig) if sig else "目前沒有明顯訊號"))
    r = repo.latest_report(s, sid)
    if r is not None:
        lines.append(f"AI（{r.date}）：{r.action}（信心 {r.confidence}）{r.summary}")
        lines.append(f"完整報告：/report {sid}")
    else:
        lines.append("AI 報告：每個交易日盤後產生")
    return "\n".join(lines)


def handle(chat_id: str, text: str, fetch: list[str] | None = None) -> str:
    """回傳要回覆的文字。fetch：呼叫端傳入時，需要立刻下載資料的代號會加進去，由呼叫端在背景處理。"""
    text = text.strip()
    if text.startswith("/"):
        m = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", text, flags=re.S)
        if not m:
            return HELP
        cmd, arg = m.group(1).lower(), m.group(2).strip()
    else:  # 不加指令直接輸入代號或名稱：速覽
        cmd, arg = "quote", text
    if cmd == "start":  # 設定 TELEGRAM_DEFAULT_CHAT_IDS 需要 chat id
        return f"{HELP}\n\n你的 chat id：{chat_id}"
    if cmd == "help":
        return HELP
    arg = arg.split()[0][:20] if arg else ""
    with session_scope() as s:
        if cmd == "list":
            ids = [w.stock_id for w in s.query(Watch).filter_by(chat_id=chat_id)]
            if not ids:
                return "尚未加入自選股，使用 /watch 2330"
            rows = []
            for snap in repo.snapshots(s, ids):
                pct = f" {snap['change_pct']:+.2f}%" if snap["change_pct"] is not None else ""
                close = f" {snap['close']:g}{pct}" if snap["close"] is not None else "（資料下載中或尚無股價）"
                rows.append(f"{snap['stock_id']} {snap['name']}{close}")
            return "自選股：\n" + "\n".join(rows)
        if not arg:
            return f"請加上股票代號或名稱，例如 /{cmd} 2330"
        if cmd == "unwatch":
            sid = arg.upper()
            if s.get(Watch, (chat_id, sid)) is None:  # 舊的無效代號也要能移除，找不到才用名稱查
                found, _ = _resolve(s, arg)
                sid = found or sid
            n = s.query(Watch).filter_by(chat_id=chat_id, stock_id=sid).delete()
            return f"已移除 {sid}" if n else f"自選股裡沒有 {sid}，用 /list 查看"
        if cmd not in {"quote", "price", "signal", "report", "watch"}:
            return HELP
        sid, err = _resolve(s, arg)
        if sid is None:
            return err
        if cmd == "watch":
            if not repo.stock_known(s, sid):
                return f"查無股票 {sid}"
            limit = get_settings().max_watch_per_chat
            exists = s.get(Watch, (chat_id, sid)) is not None
            if not exists and repo.watch_count(s, chat_id) >= limit:
                return f"自選股最多 {limit} 檔，請先用 /unwatch 移除一些。"
            repo.upsert(s, Watch, [{"chat_id": chat_id, "stock_id": sid}], keys=["chat_id", "stock_id"])
            name = repo.stock_name(s, sid)
            if repo.prices_df(s, sid, limit=1).empty:
                if fetch is not None:
                    fetch.append(sid)
                    return f"已加入 {sid} {name}，正在下載資料（約 10 秒），完成後通知你。之後每個交易日盤後推播。"
                return f"已加入 {sid} {name}，資料會在下次排程時開始抓取。"
            return f"已加入 {sid} {name}，之後每個交易日盤後推播訊號與報告。"
        if cmd == "price":
            snap = repo.snapshot(s, sid)
            if snap["close"] is None:
                return f"資料庫裡還沒有 {sid} 的股價。用 /watch {sid} 加入自選股會立刻下載。"
            chg = f"{snap['change']:+g}（{snap['change_pct']:+.2f}%）" if snap["change"] is not None else ""
            vol = f"\n成交量 {snap['volume']:,} 股" if snap["volume"] is not None else ""
            return f"{sid} {snap['name']}\n{snap['date']} 收盤 {snap['close']:g} {chg}{vol}"
        if cmd == "signal":
            sig = ta.signals(repo.indicators_df(s, sid, 5), repo.prices_df(s, sid, 5))
            return f"{sid} 技術訊號\n" + ("\n".join(f"・{x}" for x in sig) if sig else "目前沒有明顯訊號")
        base = get_settings().public_base_url
        link = f"{base}/ui/gradio/?stock={sid}&view=report" if base else ""
        if cmd == "report":
            r = repo.latest_report(s, sid)
            if r is None:
                return f"{sid} 還沒有報告，每個交易日盤後產生。" + (
                    f"\n也可以到 {link} 按「重新產生報告」。" if link else "")
            text = rpt.to_markdown(r, repo.stock_name(s, sid)).replace("### ", "").replace("**", "")
            return text + (f"\n\n完整圖表：{link}" if link else "")
        return _quote(s, sid)


def fetch_and_notify(chat_id: str, stock_id: str) -> None:
    """/watch 新股票：背景下載資料（與 PWA「查看」、每日排程共用），完成後再發一則速覽。"""
    from jobs.daily_etl import sync_stock

    try:
        latest = sync_stock(stock_id, 400)
    except ds.QuotaExceededError:
        return send_telegram(chat_id, f"FinMind 使用量已達上限，{stock_id} 會在下次排程時下載。")
    except Exception:
        log.exception("即時擷取 %s 失敗", stock_id)
        return send_telegram(chat_id, f"{stock_id} 資料下載失敗，下次排程會再試。")
    if latest is None:
        return send_telegram(chat_id, f"FinMind 沒有 {stock_id} 的股價資料，請確認代號。")
    with session_scope() as s:
        send_telegram(chat_id, "資料已就緒\n" + _quote(s, stock_id))


def sync_webhook() -> str:
    """把 webhook 指向本服務並設定指令選單。Render 啟動時自動執行（app.main），本機可用 jobs.set_webhook。"""
    s = get_settings()
    if not (s.telegram_bot_token and s.public_base_url and s.telegram_webhook_secret):
        return "略過：未設定 TELEGRAM_BOT_TOKEN、PUBLIC_BASE_URL 或 TELEGRAM_WEBHOOK_SECRET"
    api = f"https://api.telegram.org/bot{s.telegram_bot_token}"
    url = f"{s.public_base_url}/telegram/webhook"
    r = httpx.post(f"{api}/setWebhook", timeout=15, json={
        "url": url, "allowed_updates": ["message"], "secret_token": s.telegram_webhook_secret})
    r.raise_for_status()
    httpx.post(f"{api}/setMyCommands", timeout=15, json={
        "commands": [{"command": c, "description": d} for c, d in COMMANDS]}).raise_for_status()
    return f"webhook 已設定為 {url}"


@router.post("/telegram/webhook")
async def webhook(request: Request, background: BackgroundTasks,
                  x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    secret = get_settings().telegram_webhook_secret
    if not secret:  # 沒設 secret 等於任何人都能冒充 Telegram 呼叫這個端點
        raise HTTPException(503, "TELEGRAM_WEBHOOK_SECRET 未設定")
    if not hmac.compare_digest(x_telegram_bot_api_secret_token or "", secret):
        raise HTTPException(403, "invalid secret")
    update = await request.json()
    msg = update.get("message") or update.get("edited_message") or {}
    text, chat = msg.get("text"), msg.get("chat", {}).get("id")
    if text and not text.startswith("/") and msg.get("chat", {}).get("type", "private") != "private":
        return {"ok": True}  # 群組裡的一般對話不回應，只回指令
    if text and chat:
        from starlette.concurrency import run_in_threadpool

        fetch: list[str] = []
        try:
            reply = await run_in_threadpool(handle, str(chat), text, fetch)
        except Exception:  # 一律回 200：回 5xx 的話 Telegram 會一直重送同一則訊息
            log.exception("處理 Telegram 訊息失敗：%r", text[:100])
            reply = "處理時發生錯誤，請稍後再試。"
        await run_in_threadpool(send_telegram, chat, reply)
        for sid in fetch:  # 下載約 10 秒：先回 200 給 Telegram，再於背景下載，避免 Telegram 逾時重送
            background.add_task(fetch_and_notify, str(chat), sid)
    return {"ok": True}
