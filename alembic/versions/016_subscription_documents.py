"""Index subscriptions by the documents their bundles touch

Lets a publish find subscriptions whose bundle already includes a changed
document without loading every bundle.

Revision ID: 016
Revises: 015
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = '016'
down_revision: Union[str, None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BACKFILL_SQL = """
UPDATE subscriptions s SET documents = COALESCE((
    SELECT array_agg(DISTINCT d ORDER BY d) FROM (
        SELECT COALESCE(m->>'document', m->>'data_key') AS d
          FROM jsonb_each(s.bundle) kv, jsonb_array_elements(kv.value) m
        UNION
        SELECT COALESCE(
                 l->>'document',
                 (SELECT e.data_key FROM embeddings e
                   WHERE e.project_id = s.project_id AND e.node_key = l->>'data_key' LIMIT 1),
                 l->>'data_key')
          FROM jsonb_each(s.bundle) kv, jsonb_array_elements(kv.value) m,
               jsonb_array_elements(COALESCE(m->'links', '[]'::jsonb)) l
    ) x WHERE d IS NOT NULL
), '{}')
"""


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("documents", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index(
        "idx_subscriptions_documents", "subscriptions", ["documents"], postgresql_using="gin"
    )
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_index("idx_subscriptions_documents", table_name="subscriptions")
    op.drop_column("subscriptions", "documents")
