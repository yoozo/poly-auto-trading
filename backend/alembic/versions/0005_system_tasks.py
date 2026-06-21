"""system tasks

Revision ID: 0005_system_tasks
Revises: 0004_indicator_backfill_tasks
Create Date: 2026-06-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_system_tasks"
down_revision: str | None = "0004_indicator_backfill_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM indicator_snapshots")
    op.execute("DELETE FROM candles")
    op.drop_index("ix_indicator_backfill_progress_task_status", table_name="indicator_backfill_progress")
    op.drop_index("ix_indicator_backfill_progress_status", table_name="indicator_backfill_progress")
    op.drop_table("indicator_backfill_progress")
    op.drop_index("ix_indicator_backfill_tasks_status_created", table_name="indicator_backfill_tasks")
    op.drop_index("ix_indicator_backfill_tasks_status", table_name="indicator_backfill_tasks")
    op.drop_table("indicator_backfill_tasks")
    op.drop_index("ix_kline_backfill_progress_task_status", table_name="kline_backfill_progress")
    op.drop_index("ix_kline_backfill_progress_status", table_name="kline_backfill_progress")
    op.drop_table("kline_backfill_progress")
    op.drop_index("ix_kline_backfill_tasks_status_created", table_name="kline_backfill_tasks")
    op.drop_index("ix_kline_backfill_tasks_status", table_name="kline_backfill_tasks")
    op.drop_table("kline_backfill_tasks")

    op.create_table(
        "system_tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_type", sa.String(length=64), nullable=False),
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
    op.create_index("ix_system_tasks_task_type", "system_tasks", ["task_type"])
    op.create_index("ix_system_tasks_status", "system_tasks", ["status"])
    op.create_index("ix_system_tasks_type_status_created", "system_tasks", ["task_type", "status", "created_at"])
    op.create_index("ix_system_tasks_type_symbol", "system_tasks", ["task_type", "symbol"])

    op.create_table(
        "system_task_steps",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("step_key", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("cursor_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("target_ms", sa.BigInteger()),
        sa.Column("inserted_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("raw_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["task_id"], ["system_tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", "step_key", name="uq_system_task_steps_task_key"),
    )
    op.create_index("ix_system_task_steps_status", "system_task_steps", ["status"])
    op.create_index("ix_system_task_steps_task_status", "system_task_steps", ["task_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_system_task_steps_task_status", table_name="system_task_steps")
    op.drop_index("ix_system_task_steps_status", table_name="system_task_steps")
    op.drop_table("system_task_steps")
    op.drop_index("ix_system_tasks_type_symbol", table_name="system_tasks")
    op.drop_index("ix_system_tasks_type_status_created", table_name="system_tasks")
    op.drop_index("ix_system_tasks_status", table_name="system_tasks")
    op.drop_index("ix_system_tasks_task_type", table_name="system_tasks")
    op.drop_table("system_tasks")

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
    op.create_index("ix_kline_backfill_tasks_status_created", "kline_backfill_tasks", ["status", "created_at"])
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
    op.create_index("ix_kline_backfill_progress_task_status", "kline_backfill_progress", ["task_id", "status"])

    op.create_table(
        "indicator_backfill_tasks",
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
    op.create_index("ix_indicator_backfill_tasks_status", "indicator_backfill_tasks", ["status"])
    op.create_index("ix_indicator_backfill_tasks_status_created", "indicator_backfill_tasks", ["status", "created_at"])
    op.create_table(
        "indicator_backfill_progress",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("next_start_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("inserted_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["task_id"], ["indicator_backfill_tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", "interval", name="uq_indicator_backfill_progress_task_interval"),
    )
    op.create_index("ix_indicator_backfill_progress_status", "indicator_backfill_progress", ["status"])
    op.create_index("ix_indicator_backfill_progress_task_status", "indicator_backfill_progress", ["task_id", "status"])
