"""Drop tenant plans, quotas and usage

Nothing enforces tenant quotas or reads usage counters.

Revision ID: 025
Revises: 024
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '025'
down_revision: Union[str, None] = '024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('tenant_usage')
    op.drop_column('tenants', 'plan')
    op.drop_column('tenants', 'quotas')


def downgrade() -> None:
    op.add_column('tenants', sa.Column('quotas', postgresql.JSONB, nullable=False, server_default='{}'))
    op.add_column('tenants', sa.Column('plan', sa.String(50), nullable=False, server_default='free'))
    op.create_table(
        'tenant_usage',
        sa.Column('tenant_id', sa.String(255), sa.ForeignKey('tenants.tenant_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('projects_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('agents_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('api_keys_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('events_this_month', sa.Integer, nullable=False, server_default='0'),
        sa.Column('storage_used_mb', sa.Float, nullable=False, server_default='0.0'),
        sa.Column('last_updated', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
