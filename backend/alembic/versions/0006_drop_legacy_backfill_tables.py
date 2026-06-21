"""drop legacy backfill tables

Revision ID: 0006_drop_legacy_backfill_tables
Revises: 0005_system_tasks
Create Date: 2026-06-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_drop_legacy_backfill_tables"
down_revision: str | None = "0005_system_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0005 已经在新环境里删除旧表；本迁移用于已经执行过旧版 0005 的环境兜底清理。
    op.execute("DROP TABLE IF EXISTS indicator_backfill_progress CASCADE")
    op.execute("DROP TABLE IF EXISTS indicator_backfill_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS kline_backfill_progress CASCADE")
    op.execute("DROP TABLE IF EXISTS kline_backfill_tasks CASCADE")


def downgrade() -> None:
    # 旧任务历史按全新 system task 策略废弃，回滚本迁移不恢复 legacy 表。
    pass
