from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    account_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("accounts.id", ondelete="SET NULL")
    )
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
    account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("accounts.id", ondelete="CASCADE")
    )
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


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MarketMetadata(Base):
    __tablename__ = "market_metadata"

    slug: Mapped[str] = mapped_column(String(255), primary_key=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    outcome: Mapped[str | None] = mapped_column(String(128))
    raw_outcome: Mapped[str | None] = mapped_column(String(128))
    event: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    market: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("signal_key", "dedupe_key", name="uq_signals_signal_dedupe"),
        Index("ix_signals_target_created", "target_type", "target_key", "created_at"),
        Index("ix_signals_signal_created", "signal_key", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_key: Mapped[str] = mapped_column(String(96))
    signal_label: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(16))
    target_type: Mapped[str] = mapped_column(String(48))
    target_key: Mapped[str] = mapped_column(String(255))
    dedupe_key: Mapped[str] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    score: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    signal_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("channel", "delivery_key", name="uq_notification_deliveries_channel_key"),
        Index(
            "ix_notification_deliveries_target_created", "target_type", "target_key", "created_at"
        ),
        Index("ix_notification_deliveries_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(32), default="telegram")
    delivery_key: Mapped[str] = mapped_column(String(255))
    target_type: Mapped[str] = mapped_column(String(48))
    target_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotificationDeliverySignal(Base):
    __tablename__ = "notification_delivery_signals"

    notification_delivery_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notification_deliveries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    signal_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("signals.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Candle(Base, TimestampMixin):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "interval", "open_time", name="uq_candles_symbol_interval_open_time"
        ),
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


class KlineBackfillTask(Base, TimestampMixin):
    __tablename__ = "kline_backfill_tasks"
    __table_args__ = (
        Index("ix_kline_backfill_tasks_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    total_inserted: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class KlineBackfillProgress(Base, TimestampMixin):
    __tablename__ = "kline_backfill_progress"
    __table_args__ = (
        UniqueConstraint("task_id", "interval", name="uq_kline_backfill_progress_task_interval"),
        Index("ix_kline_backfill_progress_task_status", "task_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("kline_backfill_tasks.id", ondelete="CASCADE")
    )
    interval: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), index=True)
    next_start_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    inserted_count: Mapped[int] = mapped_column(BigInteger, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IndicatorSnapshot(Base):
    __tablename__ = "indicator_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "interval", "candle_time", name="uq_indicator_symbol_interval_candle_time"
        ),
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


class IndicatorBackfillTask(Base, TimestampMixin):
    __tablename__ = "indicator_backfill_tasks"
    __table_args__ = (
        Index("ix_indicator_backfill_tasks_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    total_inserted: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class IndicatorBackfillProgress(Base, TimestampMixin):
    __tablename__ = "indicator_backfill_progress"
    __table_args__ = (
        UniqueConstraint("task_id", "interval", name="uq_indicator_backfill_progress_task_interval"),
        Index("ix_indicator_backfill_progress_task_status", "task_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("indicator_backfill_tasks.id", ondelete="CASCADE")
    )
    interval: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), index=True)
    next_start_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    inserted_count: Mapped[int] = mapped_column(BigInteger, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
