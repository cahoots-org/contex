"""Add content_hash to embeddings for skip-unchanged dedup

Lets ingest skip encode + upsert for nodes whose content hasn't changed
(issue #223). Nullable: existing rows have no hash and simply re-embed once,
then settle.

Revision ID: 014
Revises: 013
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '014'
down_revision: Union[str, None] = '013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "embeddings",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("embeddings", "content_hash")
