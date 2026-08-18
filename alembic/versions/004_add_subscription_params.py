"""Add top_k and threshold columns to subscriptions

Revision ID: 004
Revises: 003
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('subscriptions', sa.Column('top_k', sa.Integer(), nullable=True))
    op.add_column('subscriptions', sa.Column('threshold', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('subscriptions', 'threshold')
    op.drop_column('subscriptions', 'top_k')
