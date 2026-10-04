"""資料表定義。主鍵一律是（股票代號, 日期），寫入用 upsert，排程重跑不會重複。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class Stock(Base):
    __tablename__ = "stocks"
    stock_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(50))
    industry: Mapped[str | None] = mapped_column(String(50))
    market: Mapped[str | None] = mapped_column(String(10))  # twse 上市、tpex 上櫃、emerging 興櫃（FinMind type）


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    stock_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)


class Indicator(Base):
    __tablename__ = "indicators"
    stock_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    ma5: Mapped[float | None] = mapped_column(Float)
    ma20: Mapped[float | None] = mapped_column(Float)
    ma60: Mapped[float | None] = mapped_column(Float)
    rsi14: Mapped[float | None] = mapped_column(Float)
    k: Mapped[float | None] = mapped_column(Float)
    d: Mapped[float | None] = mapped_column(Float)
    macd: Mapped[float | None] = mapped_column(Float)
    macd_signal: Mapped[float | None] = mapped_column(Float)
    macd_hist: Mapped[float | None] = mapped_column(Float)
    bb_upper: Mapped[float | None] = mapped_column(Float)
    bb_lower: Mapped[float | None] = mapped_column(Float)


class MonthlyRevenue(Base):
    __tablename__ = "monthly_revenue"
    stock_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[int] = mapped_column(Integer, primary_key=True)
    revenue: Mapped[int | None] = mapped_column(BigInteger)


class Institutional(Base):
    """三大法人買賣超（股數，正為買超）。FinMind TaiwanStockInstitutionalInvestorsBuySell 依法人別彙總。"""
    __tablename__ = "institutional"
    stock_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    foreign_net: Mapped[int | None] = mapped_column(BigInteger)   # 外資（含外資自營商）
    trust_net: Mapped[int | None] = mapped_column(BigInteger)     # 投信
    dealer_net: Mapped[int | None] = mapped_column(BigInteger)    # 自營商（自行買賣＋避險）


class Valuation(Base):
    """本益比、股價淨值比、殖利率。FinMind TaiwanStockPER。"""
    __tablename__ = "valuations"
    stock_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    per: Mapped[float | None] = mapped_column(Float)
    pbr: Mapped[float | None] = mapped_column(Float)
    dividend_yield: Mapped[float | None] = mapped_column(Float)


class News(Base):
    __tablename__ = "news"
    __table_args__ = (UniqueConstraint("stock_id", "link", name="uq_news_stock_link"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[str] = mapped_column(String(10), index=True)
    date: Mapped[datetime | None] = mapped_column(DateTime)
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(100))
    link: Mapped[str] = mapped_column(Text)


class Report(Base):
    __tablename__ = "reports"
    stock_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    action: Mapped[str] = mapped_column(String(10))          # 買進 / 觀望 / 賣出
    confidence: Mapped[int] = mapped_column(Integer)         # 0–100
    summary: Mapped[str] = mapped_column(Text)
    reasons: Mapped[str] = mapped_column(Text)               # 以換行分隔
    risks: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(30))        # gemini / groq / ... / rules
    model: Mapped[str] = mapped_column(String(100))
    # 同一天的規則式判斷：LLM 成功時也照算，回測才能在相同日期比較兩者
    rule_action: Mapped[str | None] = mapped_column(String(10))
    rule_confidence: Mapped[int | None] = mapped_column(Integer)
    context: Mapped[str | None] = mapped_column(Text)        # 當時送給 LLM 的資料，用來檢查理由是否有根據
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Watch(Base):
    """Telegram 使用者的自選股，也會併入每日排程的股票清單。"""
    __tablename__ = "watchlist"
    chat_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    stock_id: Mapped[str] = mapped_column(String(10), primary_key=True)
