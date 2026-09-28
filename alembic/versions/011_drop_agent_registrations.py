"""Drop the agent_registrations table

The agent-registration + webhook-push subsystem was removed with the REST API
teardown (#189, #185). MCP resource-subscription push is now the sole
notification mechanism, so the durable agent_registrations table is dead.

Revision ID: 011
Revises: 010
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '011'
down_revision: Union[str, None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('idx_agent_last_seen', table_name='agent_registrations')
    op.drop_index('idx_agent_tenant', table_name='agent_registrations')
    op.drop_index('idx_agent_project', table_name='agent_registrations')
    op.drop_table('agent_registrations')


def downgrade() -> None:
    # Recreate the table as it stood at revision 010 (001 columns + created_by).
    op.create_table(
        'agent_registrations',
        sa.Column('agent_id', sa.String(255), primary_key=True),
        sa.Column('project_id', sa.String(255), nullable=False),
        sa.Column('tenant_id', sa.String(255), sa.ForeignKey('tenants.tenant_id', ondelete='CASCADE'), nullable=True),
        sa.Column('needs', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
        sa.Column('notification_method', sa.String(20), nullable=False, server_default='mcp'),
        sa.Column('response_format', sa.String(20), nullable=False, server_default='json'),
        sa.Column('notification_channel', sa.String(255), nullable=True),
        sa.Column('webhook_url', sa.Text, nullable=True),
        sa.Column('webhook_secret', sa.Text, nullable=True),
        sa.Column('data_keys', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
        sa.Column('last_sequence', sa.String(255), nullable=True),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('data', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('idx_agent_project', 'agent_registrations', ['project_id'])
    op.create_index('idx_agent_tenant', 'agent_registrations', ['tenant_id'])
    op.create_index('idx_agent_last_seen', 'agent_registrations', ['last_seen'])
