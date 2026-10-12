"""Drop audit_events

Nothing writes or reads audit events.

Revision ID: 024
Revises: 023
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '024'
down_revision: Union[str, None] = '023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('audit_events')


def downgrade() -> None:
    op.create_table(
        'audit_events',
        sa.Column('event_id', postgresql.UUID(as_uuid=False), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False, server_default='info'),
        sa.Column('actor_id', sa.String(255), nullable=True),
        sa.Column('actor_type', sa.String(50), nullable=True),
        sa.Column('actor_ip', sa.String(50), nullable=True),
        sa.Column('actor_user_agent', sa.Text, nullable=True),
        sa.Column('tenant_id', sa.String(255), nullable=True),
        sa.Column('project_id', sa.String(255), nullable=True),
        sa.Column('resource_type', sa.String(50), nullable=True),
        sa.Column('resource_id', sa.String(255), nullable=True),
        sa.Column('action', sa.Text, nullable=False),
        sa.Column('details', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('result', sa.String(20), nullable=False, server_default='success'),
        sa.Column('request_id', sa.String(255), nullable=True),
        sa.Column('endpoint', sa.String(512), nullable=True),
        sa.Column('method', sa.String(10), nullable=True),
        sa.Column('before_state', postgresql.JSONB, nullable=True),
        sa.Column('after_state', postgresql.JSONB, nullable=True),
    )
    op.create_index('idx_audit_tenant', 'audit_events', ['tenant_id'])
    op.create_index('idx_audit_actor', 'audit_events', ['actor_id'])
    op.create_index('idx_audit_type', 'audit_events', ['event_type'])
    op.create_index('idx_audit_timestamp', 'audit_events', ['timestamp'])
