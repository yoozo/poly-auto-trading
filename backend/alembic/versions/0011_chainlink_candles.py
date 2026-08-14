"""chainlink candle metadata and nullable values

Revision ID: 0011_chainlink_candles
Revises: 0010_chainlink_twap_observations
Create Date: 2026-08-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0011_chainlink_candles"
down_revision: str | None = "0010_chainlink_twap_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0010 已在生产执行，spot observation 没有 E18 字段，必须通过新迁移放宽约束。
    op.alter_column(
        "chainlink_twap_observations",
        "full_accuracy_value",
        existing_type=sa.String(length=96),
        nullable=True,
    )
    op.add_column(
        "candles",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="binance"),
    )
    op.add_column(
        "candles",
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column(
        "candles",
        "volume",
        existing_type=sa.Numeric(28, 10),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("UPDATE candles SET volume = 0 WHERE volume IS NULL")
    op.alter_column(
        "candles",
        "volume",
        existing_type=sa.Numeric(28, 10),
        nullable=False,
    )
    op.drop_column("candles", "is_complete")
    op.drop_column("candles", "source")
    op.execute(
        "UPDATE chainlink_twap_observations "
        "SET full_accuracy_value = CAST(value * 1000000000000000000 AS TEXT) "
        "WHERE full_accuracy_value IS NULL"
    )
    op.alter_column(
        "chainlink_twap_observations",
        "full_accuracy_value",
        existing_type=sa.String(length=96),
        nullable=False,
    )
