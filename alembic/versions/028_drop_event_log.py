"""Drop the event log

Nothing writes or reads events, and they kept copies of deleted content.

Revision ID: 028
Revises: 026
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '028'
down_revision: Union[str, None] = '026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('events')
    op.drop_table('event_sequence_counters')


def downgrade() -> None:
    op.create_table(
        'event_sequence_counters',
        sa.Column('project_id', sa.String(255), primary_key=True),
        sa.Column('last_sequence', sa.BigInteger, nullable=False, server_default='0'),
    )
    op.create_table(
        'events',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.String(255), nullable=False),
        sa.Column('tenant_id', sa.String(255), sa.ForeignKey('tenants.tenant_id', ondelete='CASCADE'),
                  nullable=False, server_default='default'),
        sa.Column('event_type', sa.Text, nullable=False),
        sa.Column('data', postgresql.JSONB, nullable=False),
        sa.Column('sequence', sa.BigInteger, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('source', sa.String(50), nullable=False, server_default='api'),
        sa.Column('actor_id', sa.String(255), nullable=True),
        sa.Column('actor_type', sa.String(50), nullable=True),
        sa.Column('actor_ip', sa.String(50), nullable=True),
        sa.Column('data_key', sa.Text, nullable=True),
    )
    op.create_index('idx_events_project_sequence', 'events', ['project_id', 'sequence'], unique=True)
    op.create_index('idx_events_project_created', 'events', ['project_id', 'created_at'])
    op.create_index('idx_events_tenant', 'events', ['tenant_id'])
    op.create_index('idx_events_project_data_key_sequence', 'events',
                    ['project_id', 'data_key', sa.text('sequence DESC')])
