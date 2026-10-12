"""Drop service accounts, webhooks and snapshots

No model reads or writes these tables.

Revision ID: 027
Revises: 026
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '027'
down_revision: Union[str, None] = '026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ('webhook_deliveries', 'webhook_endpoints', 'snapshots',
                  'service_account_keys', 'service_accounts'):
        op.drop_table(table)


def downgrade() -> None:
    op.create_table(
        'service_accounts',
        sa.Column('account_id', sa.String(255), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('account_type', sa.String(50), nullable=False),
        sa.Column('tenant_id', sa.String(255), sa.ForeignKey('tenants.tenant_id', ondelete='RESTRICT'),
                  nullable=False, server_default='default'),
        sa.Column('role', sa.String(50), nullable=False, server_default='readonly'),
        sa.Column('allowed_projects', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
        sa.Column('scopes', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
        sa.Column('keys', postgresql.JSONB, nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('last_active', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_requests', sa.Integer, nullable=False, server_default='0'),
    )
    op.create_table(
        'service_account_keys',
        sa.Column('key_hash', sa.String(255), primary_key=True),
        sa.Column('account_id', sa.String(255), sa.ForeignKey('service_accounts.account_id', ondelete='CASCADE'), nullable=False),
        sa.Column('key_id', sa.String(255), nullable=False),
    )
    op.create_table(
        'snapshots',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.String(255), nullable=False),
        sa.Column('sequence', sa.String(255), nullable=False),
        sa.Column('timestamp', sa.Float, nullable=False),
        sa.Column('data', postgresql.JSONB, nullable=False),
        sa.Column('metadata', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_snapshots_project', 'snapshots', ['project_id'])
    op.create_index('idx_snapshots_project_sequence', 'snapshots', ['project_id', 'sequence'], unique=True)
    op.create_table(
        'webhook_endpoints',
        sa.Column('endpoint_id', sa.String(255), primary_key=True),
        sa.Column('tenant_id', sa.String(255), sa.ForeignKey('tenants.tenant_id', ondelete='CASCADE'), nullable=True),
        sa.Column('url', sa.Text, nullable=False),
        sa.Column('secret', sa.Text, nullable=False),
        sa.Column('events', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
        sa.Column('categories', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
        sa.Column('project_ids', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('timeout_seconds', sa.Integer, nullable=False, server_default='30'),
        sa.Column('max_retries', sa.Integer, nullable=False, server_default='3'),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('idx_webhook_tenant', 'webhook_endpoints', ['tenant_id'])
    op.create_index('idx_webhook_active', 'webhook_endpoints', ['is_active'])
    op.create_table(
        'webhook_deliveries',
        sa.Column('delivery_id', sa.String(255), primary_key=True),
        sa.Column('event_id', sa.String(255), nullable=False),
        sa.Column('endpoint_id', sa.String(255), sa.ForeignKey('webhook_endpoints.endpoint_id', ondelete='CASCADE'), nullable=False),
        sa.Column('attempt', sa.Integer, nullable=False, server_default='1'),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('status_code', sa.Integer, nullable=True),
        sa.Column('response_body', sa.Text, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Float, nullable=True),
    )
    op.create_index('idx_webhook_delivery_endpoint', 'webhook_deliveries', ['endpoint_id'])
