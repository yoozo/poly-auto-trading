"""candle unavailable ranges

Revision ID: 0008_candle_unavailable_ranges
Revises: 0007_simplify_system_task_steps
Create Date: 2026-06-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008_candle_unavailable_ranges"
down_revision: str | None = "0007_simplify_system_task_steps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candle_unavailable_ranges",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="binance_rest"),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "source",
            "symbol",
            "interval",
            "start_ms",
            "end_ms",
            name="uq_candle_unavailable_range",
        ),
    )
    op.create_index(
        "ix_candle_unavailable_symbol_interval",
        "candle_unavailable_ranges",
        ["symbol", "interval", "start_ms", "end_ms"],
    )
    op.execute(
        """
        INSERT INTO candle_unavailable_ranges (source, symbol, interval, start_ms, end_ms, reason)
        SELECT DISTINCT
            'binance_rest',
            t.symbol,
            s.interval,
            s.start_ms,
            s.end_ms,
            'migrated from completed empty kline step'
        FROM system_task_steps s
        JOIN system_tasks t ON t.id = s.task_id
        WHERE t.task_type = 'kline_backfill'
          AND s.status = 'completed'
          AND s.raw_count = 0
          AND s.inserted_count = 0
          AND s.end_ms IS NOT NULL
          AND s.start_ms <= s.end_ms
        ON CONFLICT ON CONSTRAINT uq_candle_unavailable_range DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_candle_unavailable_symbol_interval", table_name="candle_unavailable_ranges")
    op.drop_table("candle_unavailable_ranges")
