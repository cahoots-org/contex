"""pg_search BM25 index; drop search_text tsvector

Revision ID: 008
"""
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_search')
    # BM25 index over the raw text columns, keyed on the int PK; project_id is
    # included so the per-project WHERE filter is served by the index.
    op.execute(
        "CREATE INDEX embeddings_bm25 ON embeddings "
        "USING bm25 (id, description, data_original, project_id) "
        "WITH (key_field = 'id')"
    )
    # search_text (generated tsvector, migration 002) was only used by the old
    # FTS lexical path, now replaced by pg_search.
    op.drop_index("idx_embeddings_search_text", table_name="embeddings")
    op.drop_column("embeddings", "search_text")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE embeddings ADD COLUMN search_text tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', "
        "coalesce(description,'') || ' ' || coalesce(data_original,''))) STORED"
    )
    op.create_index("idx_embeddings_search_text", "embeddings", ["search_text"],
                    postgresql_using="gin")
    op.execute("DROP INDEX IF EXISTS embeddings_bm25")
