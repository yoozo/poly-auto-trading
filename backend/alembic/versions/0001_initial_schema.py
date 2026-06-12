"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("input", sa.String(length=255), nullable=False),
        sa.Column("normalized_user", sa.String(length=255), nullable=False),
        sa.Column("proxy_wallet", sa.String(length=64), nullable=False, index=True),
        sa.Column("profile", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("favorite", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_downloaded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "analysis_tasks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("account_id", sa.String(length=64), sa.ForeignKey("accounts.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_analysis_tasks_status", "analysis_tasks", ["status"])

    op.create_table(
        "activities",
        sa.Column("id", sa.String(length=96), primary_key=True),
        sa.Column("account_id", sa.String(length=64), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proxy_wallet", sa.String(length=64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("condition_id", sa.String(length=128)),
        sa.Column("slug", sa.String(length=255)),
        sa.Column("event_slug", sa.String(length=255)),
        sa.Column("title", sa.Text()),
        sa.Column("side", sa.String(length=16)),
        sa.Column("outcome", sa.String(length=128)),
        sa.Column("asset", sa.String(length=128)),
        sa.Column("price", sa.Numeric(20, 8)),
        sa.Column("size", sa.Numeric(28, 10)),
        sa.Column("usdc_size", sa.Numeric(28, 10)),
        sa.Column("transaction_hash", sa.String(length=128)),
        sa.Column("raw", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_activities_account_timestamp", "activities", ["account_id", "timestamp"])
    op.create_index("ix_activities_slug", "activities", ["slug"])
    op.create_index("ix_activities_type", "activities", ["type"])

    op.create_table(
        "market_metadata",
        sa.Column("slug", sa.String(length=255), primary_key=True),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("outcome", sa.String(length=128)),
        sa.Column("raw_outcome", sa.String(length=128)),
        sa.Column("event", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("market", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_market_metadata_closed", "market_metadata", ["closed"])

    op.create_table(
        "candles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(28, 10), nullable=False),
        sa.Column("high", sa.Numeric(28, 10), nullable=False),
        sa.Column("low", sa.Numeric(28, 10), nullable=False),
        sa.Column("close", sa.Numeric(28, 10), nullable=False),
        sa.Column("volume", sa.Numeric(28, 10), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("symbol", "interval", "open_time", name="uq_candles_symbol_interval_open_time"),
    )
    op.create_index("ix_candles_symbol_interval_close_time", "candles", ["symbol", "interval", "close_time"])

    op.create_table(
        "indicator_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("candle_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rsi", sa.Numeric(16, 8)),
        sa.Column("rsi_ema", sa.Numeric(16, 8)),
        sa.Column("rsi_ema_diff", sa.Numeric(16, 8)),
        sa.Column("boll_upper", sa.Numeric(28, 10)),
        sa.Column("boll_middle", sa.Numeric(28, 10)),
        sa.Column("boll_lower", sa.Numeric(28, 10)),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("symbol", "interval", "candle_time", name="uq_indicator_symbol_interval_candle_time"),
    )
    op.create_index("ix_indicator_snapshots_symbol_interval_time", "indicator_snapshots", ["symbol", "interval", "candle_time"])

    op.create_table(
        "service_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_service_events_service_created_at", "service_events", ["service", "created_at"])
    op.create_index("ix_service_events_level", "service_events", ["level"])


def downgrade() -> None:
    op.drop_table("service_events")
    op.drop_table("indicator_snapshots")
    op.drop_table("candles")
    op.drop_table("market_metadata")
    op.drop_table("activities")
    op.drop_table("analysis_tasks")
    op.drop_table("accounts")

