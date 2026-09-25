"""Record agent registration ownership

Revision ID: 010
Revises: 009
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '010'
down_revision: Union[str, None] = '009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_registrations",
        sa.Column("created_by", sa.String(length=255), nullable=True),
    )
    # Legacy agents predate ownership tracking; the ``system`` marker lets them
    # keep accepting re-registration instead of locking existing agents out.
    op.execute(
        "UPDATE agent_registrations SET created_by = 'system' "
        "WHERE created_by IS NULL"
    )


def downgrade() -> None:
    op.drop_column("agent_registrations", "created_by")
