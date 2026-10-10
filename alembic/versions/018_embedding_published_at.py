"""Caller-supplied publish time on embeddings

``published_at`` records when the source says the content changed, so time
windows treat backfilled history as old rather than as just ingested. NULL
falls back to ``updated_at`` / ``created_at``.

Revision ID: 018
Revises: 017
Create Date: 2026-10-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '018'
down_revision: Union[str, None] = '017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("embeddings", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("embeddings", "published_at")
