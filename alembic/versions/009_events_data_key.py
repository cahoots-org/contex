"""Denormalize data_key onto events for key-scoped version queries

Revision ID: 009
Revises: 008
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '009'
down_revision: Union[str, None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("data_key", sa.String(length=255), nullable=True))
    # Existing events are published as a single-key ``{data_key: value}`` payload,
    # so the denormalized column is the sole top-level JSONB key of ``data``.
    op.execute(
        "UPDATE events "
        "SET data_key = (SELECT k FROM jsonb_object_keys(data) AS k LIMIT 1) "
        "WHERE data_key IS NULL "
        "AND jsonb_typeof(data) = 'object'"
    )
    op.create_index(
        "idx_events_project_data_key_sequence",
        "events",
        ["project_id", "data_key", sa.text("sequence DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_events_project_data_key_sequence", table_name="events")
    op.drop_column("events", "data_key")
