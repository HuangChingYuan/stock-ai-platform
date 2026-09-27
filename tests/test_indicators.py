import numpy as np
import pandas as pd

from core import indicators as ta


def _prices(n=120, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=n).date,
                         "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1_000_000})


def test_columns_and_ranges():
    ind = ta.compute(_prices())
    for col in ("ma5", "ma20", "ma60", "rsi14", "k", "d", "macd", "macd_signal", "macd_hist", "bb_upper", "bb_lower"):
        assert col in ind
    last = ind.iloc[-1]
    assert 0 <= last.rsi14 <= 100 and 0 <= last.k <= 100 and 0 <= last.d <= 100
    assert last.bb_lower < last.ma20 < last.bb_upper
    assert ind["ma60"].iloc[:59].isna().all()


def test_rsi_all_up_is_100():
    df = _prices()
    df["close"] = np.linspace(100, 200, len(df))
    df["high"], df["low"] = df["close"] * 1.01, df["close"] * 0.99
    assert ta.compute(df)["rsi14"].iloc[-1] == 100


def test_rule_score_returns_valid_action():
    df = _prices()
    action, conf, _ = ta.rule_score(ta.compute(df), df)
    assert action in {"買進", "觀望", "賣出"} and 0 <= conf <= 100
