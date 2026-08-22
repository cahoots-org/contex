"""Add source and actor provenance columns to events

Revision ID: 005
Revises: 004
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("source", sa.String(length=50), nullable=False, server_default="api"))
    op.add_column("events", sa.Column("actor_id", sa.String(length=255), nullable=True))
    op.add_column("events", sa.Column("actor_type", sa.String(length=50), nullable=True))
    op.add_column("events", sa.Column("actor_ip", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "actor_ip")
    op.drop_column("events", "actor_type")
    op.drop_column("events", "actor_id")
    op.drop_column("events", "source")
