"""kline backfill tasks

Revision ID: 0003_kline_backfill_tasks
Revises: 0002_notifications
Create Date: 2026-06-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003_kline_backfill_tasks"
down_revision: str | None = "0002_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kline_backfill_tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("total_inserted", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_kline_backfill_tasks_status", "kline_backfill_tasks", ["status"])
    op.create_index(
        "ix_kline_backfill_tasks_status_created",
        "kline_backfill_tasks",
        ["status", "created_at"],
    )
    op.create_table(
        "kline_backfill_progress",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("next_start_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("inserted_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["task_id"], ["kline_backfill_tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", "interval", name="uq_kline_backfill_progress_task_interval"),
    )
    op.create_index("ix_kline_backfill_progress_status", "kline_backfill_progress", ["status"])
    op.create_index(
        "ix_kline_backfill_progress_task_status",
        "kline_backfill_progress",
        ["task_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_kline_backfill_progress_task_status", table_name="kline_backfill_progress")
    op.drop_index("ix_kline_backfill_progress_status", table_name="kline_backfill_progress")
    op.drop_table("kline_backfill_progress")
    op.drop_index("ix_kline_backfill_tasks_status_created", table_name="kline_backfill_tasks")
    op.drop_index("ix_kline_backfill_tasks_status", table_name="kline_backfill_tasks")
    op.drop_table("kline_backfill_tasks")
