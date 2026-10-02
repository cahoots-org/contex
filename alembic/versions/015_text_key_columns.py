"""Widen key/path columns to text

Structural parsing builds node keys from the file path plus the path inside
the file (e.g. a deep JSON path under a long repo path), which overflowed
varchar(255). Postgres stores and indexes text the same as varchar.

Revision ID: 015
Revises: 014
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '015'
down_revision: Union[str, None] = '014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = [
    ("embeddings", "data_key", 255),
    ("embeddings", "node_key", 255),
    ("embeddings", "node_path", 1024),
    ("symbols", "data_key", 255),
    ("symbols", "node_key", 255),
    ("events", "data_key", 255),
    ("events", "event_type", 255),
]


def upgrade() -> None:
    for table, column, _ in COLUMNS:
        op.alter_column(table, column, type_=sa.Text())


def downgrade() -> None:
    for table, column, length in COLUMNS:
        op.alter_column(table, column, type_=sa.String(length))
