"""chainlink twap observations

Revision ID: 0010_chainlink_twap_observations
Revises: 0009_polymarket_credentials
Create Date: 2026-08-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010_chainlink_twap_observations"
down_revision: str | None = "0009_polymarket_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chainlink_twap_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("value", sa.Numeric(38, 18), nullable=False),
        sa.Column("full_accuracy_value", sa.String(length=96), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "symbol", "window_seconds", "observed_at", name="uq_chainlink_twap_observation"
        ),
    )
    op.create_index(
        "ix_chainlink_twap_symbol_window_observed",
        "chainlink_twap_observations",
        ["symbol", "window_seconds", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chainlink_twap_symbol_window_observed",
        table_name="chainlink_twap_observations",
    )
    op.drop_table("chainlink_twap_observations")
