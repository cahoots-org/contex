"""Widen the embedding column to vector(768) for gte-base

Moving the default embedder to thenlper/gte-base (768-dim, was all-MiniLM-L6-v2
at 384). pgvector columns are fixed-dimension and cannot convert between dims,
and a stored vector is meaningless under a different model anyway — so this
CLEARS the embeddings table and the corpus must be re-embedded by re-running the
connectors. The ALTER runs on the now-empty table, so it needs no USING cast
(which fails under asyncpg — see test_migration_fresh_db).

Revision ID: 012
Revises: 011
Create Date: 2026-10-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '012'
down_revision: Union[str, None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _set_dim(dim: int) -> None:
    # Drop the HNSW index (it pins the column type), clear the model-specific
    # vectors, widen the column on the empty table, then rebuild the index.
    op.drop_index('idx_embeddings_vector', table_name='embeddings')
    op.execute('TRUNCATE TABLE embeddings')
    op.execute(f'ALTER TABLE embeddings ALTER COLUMN embedding TYPE vector({dim})')
    op.create_index(
        'idx_embeddings_vector',
        'embeddings',
        ['embedding'],
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
        postgresql_with={'m': 16, 'ef_construction': 64},
    )


def upgrade() -> None:
    _set_dim(768)


def downgrade() -> None:
    _set_dim(384)
