"""simplify system task steps

Revision ID: 0007_simplify_system_task_steps
Revises: 0006_drop_legacy_backfill_tables
Create Date: 2026-06-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007_simplify_system_task_steps"
down_revision: str | None = "0006_drop_legacy_backfill_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("system_task_steps", sa.Column("interval", sa.String(length=8)))
    op.add_column("system_task_steps", sa.Column("start_ms", sa.BigInteger(), server_default="0"))
    op.add_column("system_task_steps", sa.Column("end_ms", sa.BigInteger()))

    op.execute(
        """
        UPDATE system_task_steps
        SET
            interval = COALESCE(metadata->>'interval', split_part(step_key, ':', 1)),
            start_ms = COALESCE(NULLIF(metadata->>'start_ms', '')::bigint, cursor_ms, 0),
            end_ms = COALESCE(NULLIF(metadata->>'end_ms', '')::bigint, target_ms)
        """
    )
    op.alter_column("system_task_steps", "interval", existing_type=sa.String(length=8), nullable=False)
    op.alter_column("system_task_steps", "start_ms", existing_type=sa.BigInteger(), nullable=False)
    op.drop_column("system_task_steps", "metadata")
    op.drop_column("system_task_steps", "target_ms")


def downgrade() -> None:
    op.add_column(
        "system_task_steps",
        sa.Column("target_ms", sa.BigInteger()),
    )
    op.add_column(
        "system_task_steps",
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.execute(
        """
        UPDATE system_task_steps
        SET
            target_ms = end_ms,
            metadata = json_build_object(
                'interval', interval,
                'start_ms', start_ms,
                'end_ms', end_ms
            )
        """
    )
    op.drop_column("system_task_steps", "end_ms")
    op.drop_column("system_task_steps", "start_ms")
    op.drop_column("system_task_steps", "interval")
