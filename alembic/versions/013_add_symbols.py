"""Add symbols table for cross-file code linking

Revision ID: 013
Revises: 012
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '013'
down_revision: Union[str, None] = '012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "symbols",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("data_key", sa.String(255), nullable=False),
        sa.Column("node_key", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),  # "def" | "ref"
    )
    op.create_index(
        "idx_symbols_project_name_role", "symbols",
        ["project_id", "name", "role"],
    )
    op.create_index(
        "idx_symbols_project_node_key", "symbols",
        ["project_id", "node_key"],
    )
    op.create_index(
        "idx_symbols_project_data_key", "symbols",
        ["project_id", "data_key"],
    )


def downgrade() -> None:
    op.drop_index("idx_symbols_project_data_key", table_name="symbols")
    op.drop_index("idx_symbols_project_node_key", table_name="symbols")
    op.drop_index("idx_symbols_project_name_role", table_name="symbols")
    op.drop_table("symbols")
