"""Add tsvector search_text generated column to embeddings

Revision ID: 002
Revises: 001
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "embeddings",
        sa.Column(
            "search_text",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(description,'') || ' ' || coalesce(data_original,''))",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_embeddings_search_text", "embeddings", ["search_text"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("idx_embeddings_search_text", table_name="embeddings")
    op.drop_column("embeddings", "search_text")
