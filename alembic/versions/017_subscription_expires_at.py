"""Lease subscriptions with an expiry

A subscription lives until ``expires_at``; creating or reading it renews the
lease and the reconcile sweep deletes expired rows. Existing rows get one hour.

Revision ID: 017
Revises: 016
Create Date: 2026-10-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '017'
down_revision: Union[str, None] = '016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now() + interval '1 hour'"),
        ),
    )
    op.alter_column("subscriptions", "expires_at", server_default=None)


def downgrade() -> None:
    op.drop_column("subscriptions", "expires_at")
