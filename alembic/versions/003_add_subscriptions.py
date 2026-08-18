"""Add subscriptions table with materialized bundle

Revision ID: 003
Revises: 002
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("subscription_id", sa.String(255), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=True),
        sa.Column("needs", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=True),
        sa.Column("bundle", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("bundle_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_subscriptions_project", "subscriptions", ["project_id"])


def downgrade() -> None:
    op.drop_index("idx_subscriptions_project", table_name="subscriptions")
    op.drop_table("subscriptions")
