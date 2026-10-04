"""回測報告：比較 LLM 建議與規則式判斷的勝率。

python -m jobs.backtest                       # 全部股票，持有 5 與 20 個交易日
python -m jobs.backtest --stocks 2330 --horizons 1,5,10
python -m jobs.backtest --all-days            # 規則式改用全部報告日，不只有 LLM 報告的日子
python -m jobs.backtest --csv backtest.csv    # 另存逐筆明細
"""
from __future__ import annotations

import argparse
import os

from core import backtest as bt
from core.db import init_db, session_scope


def render(frame, horizons: list[int], paired: bool) -> str:
    out = ["# 回測結果", "",
           "勝負：買進看 N 日後上漲、賣出看 N 日後下跌，觀望不計；未計手續費與稅。"
           + ("只比較有 LLM 報告的日子。" if paired else "規則式使用全部報告日。"), ""]
    for h in horizons:
        out += [f"## 持有 {h} 個交易日", "", _markdown(bt.summarize(frame, h, paired=paired)), ""]
    return "\n".join(out)


def _markdown(df) -> str:
    if df.empty:
        return "（尚無已滿持有天數的報告）"
    fmt = lambda v: "—" if v is None or v != v else f"{v:g}" if isinstance(v, float) else str(v)
    rows = [list(df.columns), ["---"] * len(df.columns), *[[fmt(v) for v in r] for r in df.itertuples(index=False)]]
    return "\n".join("| " + " | ".join(r) + " |" for r in rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stocks", help="逗號分隔，預設為所有有報告的股票")
    p.add_argument("--horizons", default=",".join(map(str, bt.HORIZONS)), help="持有交易日數，逗號分隔")
    p.add_argument("--all-days", action="store_true", help="規則式使用全部報告日")
    p.add_argument("--csv", help="逐筆明細輸出的 CSV 路徑")
    args = p.parse_args()

    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    stocks = [x.strip().upper() for x in args.stocks.split(",") if x.strip()] if args.stocks else None
    init_db()
    with session_scope() as s:
        frame = bt.signals_frame(s, stocks, tuple(horizons))
    text = render(frame, horizons, paired=not args.all_days)
    print(text)
    if args.csv:
        frame.to_csv(args.csv, index=False, encoding="utf-8-sig")  # 帶 BOM，Excel 開啟中文不亂碼
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:  # GitHub Actions：結果顯示在該次執行的摘要頁
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
