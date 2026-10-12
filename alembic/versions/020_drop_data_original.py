"""Drop embeddings.data_original

Every node stored the whole published document, so BM25 matched text a
document no longer contains on nodes that hadn't changed, and a large
document was stored once per node. Each node's description already holds its
own text, so the BM25 index now covers that alone.

The index is rebuilt with default settings; boot rebuilds it again if
BM25_TOKENIZER, BM25_K1 or BM25_B say otherwise.

Revision ID: 020
Revises: 019
Create Date: 2026-10-11 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '020'
down_revision: Union[str, None] = '019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS embeddings_bm25")
    op.drop_column("embeddings", "data_original")
    op.execute(
        "CREATE INDEX embeddings_bm25 ON embeddings "
        "USING bm25 (id, description, project_id) WITH (key_field = 'id')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS embeddings_bm25")
    op.add_column("embeddings", sa.Column("data_original", sa.Text(), nullable=True))
    op.execute(
        "CREATE INDEX embeddings_bm25 ON embeddings "
        "USING bm25 (id, description, data_original, project_id) WITH (key_field = 'id')"
    )
