"""polymarket credentials

Revision ID: 0009_polymarket_credentials
Revises: 0008_candle_unavailable_ranges
Create Date: 2026-06-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009_polymarket_credentials"
down_revision: str | None = "0008_candle_unavailable_ranges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "polymarket_credentials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("signer_address", sa.String(length=64), nullable=False),
        sa.Column("funder_address", sa.String(length=64), nullable=False),
        sa.Column("signature_type", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("api_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("api_passphrase_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "signer_address",
            "funder_address",
            name="uq_polymarket_credentials_wallets",
        ),
    )
    op.create_index(
        "ix_polymarket_credentials_signer",
        "polymarket_credentials",
        ["signer_address"],
    )
    op.create_index(
        "ix_polymarket_credentials_funder",
        "polymarket_credentials",
        ["funder_address"],
    )

def downgrade() -> None:
    op.drop_index("ix_polymarket_credentials_funder", table_name="polymarket_credentials")
    op.drop_index("ix_polymarket_credentials_signer", table_name="polymarket_credentials")
    op.drop_table("polymarket_credentials")
