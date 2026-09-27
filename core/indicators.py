"""技術指標：純 pandas 實作，不依賴 TA-Lib，方便部署在免費主機。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """輸入欄位：date, high, low, close（依日期排序）。回傳 date + 各指標欄位。"""
    df = df.sort_values("date").reset_index(drop=True)
    close, high, low = df["close"].astype(float), df["high"].astype(float), df["low"].astype(float)
    out = pd.DataFrame({"date": df["date"]})

    # 趨勢：移動平均
    for n in (5, 20, 60):
        out[f"ma{n}"] = close.rolling(n).mean()

    # 動能：RSI（Wilder 平滑）
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi14"] = (100 - 100 / (1 + rs)).where(loss != 0, 100.0).where(gain.notna())

    # 動能：KD（台灣慣用 9,3,3，K、D 初始值 50）
    low9, high9 = low.rolling(9).min(), high.rolling(9).max()
    rsv = ((close - low9) / (high9 - low9).replace(0, np.nan) * 100).fillna(50)
    k_vals, d_vals, k, d = [], [], 50.0, 50.0
    for i, v in enumerate(rsv):
        if i < 8:
            k_vals.append(np.nan); d_vals.append(np.nan); continue
        k = k * 2 / 3 + v / 3
        d = d * 2 / 3 + k / 3
        k_vals.append(k); d_vals.append(d)
    out["k"], out["d"] = k_vals, d_vals

    # 趨勢＋動能：MACD（12, 26, 9）
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    out.loc[: 25, ["macd", "macd_signal", "macd_hist"]] = np.nan  # 樣本不足時不輸出

    # 波動：布林通道（20, 2）
    mid, std = close.rolling(20).mean(), close.rolling(20).std(ddof=0)
    out["bb_upper"], out["bb_lower"] = mid + 2 * std, mid - 2 * std
    return out


def signals(ind: pd.DataFrame, prices: pd.DataFrame) -> list[str]:
    """把最新兩日指標轉成文字訊號，給 Telegram、報告與規則式備援使用。"""
    if len(ind) < 2 or prices.empty:
        return []
    cur, prev = ind.iloc[-1], ind.iloc[-2]
    close = float(prices.iloc[-1]["close"])
    out: list[str] = []

    def ok(*vals) -> bool:
        return all(pd.notna(v) for v in vals)

    if ok(cur.ma5, cur.ma20, prev.ma5, prev.ma20):
        if prev.ma5 <= prev.ma20 and cur.ma5 > cur.ma20:
            out.append("MA5 上穿 MA20（黃金交叉）")
        elif prev.ma5 >= prev.ma20 and cur.ma5 < cur.ma20:
            out.append("MA5 下穿 MA20（死亡交叉）")
    if ok(cur.ma60):
        out.append("收盤在季線之上" if close > cur.ma60 else "收盤在季線之下")
    if ok(cur.rsi14):
        if cur.rsi14 >= 70:
            out.append(f"RSI {cur.rsi14:.0f}，偏超買")
        elif cur.rsi14 <= 30:
            out.append(f"RSI {cur.rsi14:.0f}，偏超賣")
    if ok(cur.k, cur.d, prev.k, prev.d):
        if prev.k <= prev.d and cur.k > cur.d:
            out.append(f"KD 黃金交叉（K={cur.k:.0f}）")
        elif prev.k >= prev.d and cur.k < cur.d:
            out.append(f"KD 死亡交叉（K={cur.k:.0f}）")
    if ok(cur.macd_hist, prev.macd_hist):
        if prev.macd_hist <= 0 < cur.macd_hist:
            out.append("MACD 柱狀體翻正")
        elif prev.macd_hist >= 0 > cur.macd_hist:
            out.append("MACD 柱狀體翻負")
    if ok(cur.bb_upper, cur.bb_lower):
        if close > cur.bb_upper:
            out.append("突破布林上軌")
        elif close < cur.bb_lower:
            out.append("跌破布林下軌")
    return out


def rule_score(ind: pd.DataFrame, prices: pd.DataFrame) -> tuple[str, int, list[str]]:
    """沒有可用的 LLM 時使用的規則式判斷，確保系統在零 API 金鑰下也能運作。"""
    sig = signals(ind, prices)
    score = 0
    for s in sig:
        if any(w in s for w in ("黃金交叉", "翻正", "季線之上", "偏超賣")):
            score += 1
        if any(w in s for w in ("死亡交叉", "翻負", "季線之下", "偏超買")):
            score -= 1
    action = "買進" if score >= 2 else "賣出" if score <= -2 else "觀望"
    return action, min(40 + abs(score) * 10, 70), sig
