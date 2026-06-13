"""notification settings and signals

Revision ID: 0002_notifications
Revises: 0001_initial_schema
Create Date: 2026-06-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_notifications"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("signal_key", sa.String(length=96), nullable=False),
        sa.Column("signal_label", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("target_type", sa.String(length=48), nullable=False),
        sa.Column("target_key", sa.String(length=255), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Numeric(20, 8)),
        sa.Column("input_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("signal_key", "dedupe_key", name="uq_signals_signal_dedupe"),
    )
    op.create_index(
        "ix_signals_target_created",
        "signals",
        ["target_type", "target_key", "created_at"],
    )
    op.create_index(
        "ix_signals_signal_created",
        "signals",
        ["signal_key", "created_at"],
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String(length=32), nullable=False, server_default="telegram"),
        sa.Column("delivery_key", sa.String(length=255), nullable=False),
        sa.Column("target_type", sa.String(length=48), nullable=False),
        sa.Column("target_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("channel", "delivery_key", name="uq_notification_deliveries_channel_key"),
    )
    op.create_index(
        "ix_notification_deliveries_target_created",
        "notification_deliveries",
        ["target_type", "target_key", "created_at"],
    )
    op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])
    op.create_table(
        "notification_delivery_signals",
        sa.Column("notification_delivery_id", sa.BigInteger(), nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["notification_delivery_id"], ["notification_deliveries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("notification_delivery_id", "signal_id"),
    )


def downgrade() -> None:
    op.drop_table("notification_delivery_signals")
    op.drop_index("ix_notification_deliveries_status", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_target_created", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index("ix_signals_signal_created", table_name="signals")
    op.drop_index("ix_signals_target_created", table_name="signals")
    op.drop_table("signals")
    op.drop_table("app_settings")
