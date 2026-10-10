"""Origin tag on embeddings

``origin`` names the source stream that last published a document (e.g.
``s3:bucket/prefix``), so a connector can list what it published before and
delete what is gone at the source.

Revision ID: 019
Revises: 018
Create Date: 2026-10-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '019'
down_revision: Union[str, None] = '018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("embeddings", sa.Column("origin", sa.Text(), nullable=True))
    op.create_index("idx_embeddings_project_origin", "embeddings", ["project_id", "origin", "data_key"])


def downgrade() -> None:
    op.drop_index("idx_embeddings_project_origin", table_name="embeddings")
    op.drop_column("embeddings", "origin")
