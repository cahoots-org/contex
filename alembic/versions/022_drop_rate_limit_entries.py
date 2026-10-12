"""Drop rate_limit_entries

Rate limits are counted in process memory now, so the table is unused.

Revision ID: 022
Revises: 021
Create Date: 2026-10-11 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '022'
down_revision: Union[str, None] = '021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('rate_limit_entries')


def downgrade() -> None:
    op.create_table(
        'rate_limit_entries',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('rate_key', sa.String(512), nullable=False),
        sa.Column('request_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_rate_limit_key_time', 'rate_limit_entries', ['rate_key', 'request_time'])
