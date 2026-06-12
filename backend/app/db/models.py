from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    input: Mapped[str] = mapped_column(String(255))
    normalized_user: Mapped[str] = mapped_column(String(255))
    proxy_wallet: Mapped[str] = mapped_column(String(64), index=True)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    last_downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisTask(Base, TimestampMixin):
    __tablename__ = "analysis_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("accounts.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    percent: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_account_timestamp", "account_id", "timestamp"),
        Index("ix_activities_slug", "slug"),
        Index("ix_activities_type", "type"),
    )

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), ForeignKey("accounts.id", ondelete="CASCADE"))
    proxy_wallet: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[str] = mapped_column(String(48))
    condition_id: Mapped[str | None] = mapped_column(String(128))
    slug: Mapped[str | None] = mapped_column(String(255))
    event_slug: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    side: Mapped[str | None] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(128))
    asset: Mapped[str | None] = mapped_column(String(128))
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    size: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    usdc_size: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    transaction_hash: Mapped[str | None] = mapped_column(String(128))
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketMetadata(Base):
    __tablename__ = "market_metadata"

    slug: Mapped[str] = mapped_column(String(255), primary_key=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    outcome: Mapped[str | None] = mapped_column(String(128))
    raw_outcome: Mapped[str | None] = mapped_column(String(128))
    event: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    market: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Candle(Base, TimestampMixin):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "open_time", name="uq_candles_symbol_interval_open_time"),
        Index("ix_candles_symbol_interval_close_time", "symbol", "interval", "close_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24))
    interval: Mapped[str] = mapped_column(String(8))
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    high: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    low: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    close: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    volume: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    is_closed: Mapped[bool] = mapped_column(Boolean, default=True)


class IndicatorSnapshot(Base):
    __tablename__ = "indicator_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "candle_time", name="uq_indicator_symbol_interval_candle_time"),
        Index("ix_indicator_snapshots_symbol_interval_time", "symbol", "interval", "candle_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24))
    interval: Mapped[str] = mapped_column(String(8))
    candle_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rsi: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    rsi_ema: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    rsi_ema_diff: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    boll_upper: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    boll_middle: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    boll_lower: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServiceEvent(Base):
    __tablename__ = "service_events"
    __table_args__ = (
        Index("ix_service_events_service_created_at", "service", "created_at"),
        Index("ix_service_events_level", "level"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
