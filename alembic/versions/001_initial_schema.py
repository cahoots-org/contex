"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable required extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')

    # Tenants table
    op.create_table(
        'tenants',
        sa.Column('tenant_id', sa.String(255), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('plan', sa.String(50), nullable=False, server_default='free'),
        sa.Column('quotas', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('settings', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('metadata', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('owner_email', sa.String(255), nullable=True),
        sa.Column('billing_email', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Tenant usage table
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

    # Tenant projects table
    op.create_table(
        'tenant_projects',
        sa.Column('tenant_id', sa.String(255), sa.ForeignKey('tenants.tenant_id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('tenant_id', 'project_id'),
    )

    # API keys table
    op.create_table(
        'api_keys',
        sa.Column('key_id', sa.String(255), primary_key=True),
        sa.Column('key_hash', sa.String(255), unique=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('prefix', sa.String(10), nullable=False),
        sa.Column('scopes', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
        sa.Column('tenant_id', sa.String(255), sa.ForeignKey('tenants.tenant_id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_api_keys_hash', 'api_keys', ['key_hash'])

    # API key roles table
    op.create_table(
        'api_key_roles',
        sa.Column('key_id', sa.String(255), sa.ForeignKey('api_keys.key_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('role', sa.String(50), nullable=False, server_default='readonly'),
        sa.Column('projects', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
    )

    # Service accounts table
    op.create_table(
        'service_accounts',
        sa.Column('account_id', sa.String(255), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('account_type', sa.String(50), nullable=False),
        sa.Column('tenant_id', sa.String(255), sa.ForeignKey('tenants.tenant_id', ondelete='SET NULL'), nullable=True),
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

    # Service account keys table
    op.create_table(
        'service_account_keys',
        sa.Column('key_hash', sa.String(255), primary_key=True),
        sa.Column('account_id', sa.String(255), sa.ForeignKey('service_accounts.account_id', ondelete='CASCADE'), nullable=False),
        sa.Column('key_id', sa.String(255), nullable=False),
    )

    # Events table (event sourcing)
    op.create_table(
        'events',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.String(255), nullable=False),
        sa.Column('tenant_id', sa.String(255), sa.ForeignKey('tenants.tenant_id', ondelete='CASCADE'), nullable=True),
        sa.Column('event_type', sa.String(255), nullable=False),
        sa.Column('data', postgresql.JSONB, nullable=False),
        sa.Column('sequence', sa.BigInteger, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_events_project_sequence', 'events', ['project_id', 'sequence'], unique=True)
    op.create_index('idx_events_project_created', 'events', ['project_id', 'created_at'])
    op.create_index('idx_events_tenant', 'events', ['tenant_id'])

    # Snapshots table
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

    # Embeddings table with pgvector
    op.create_table(
        'embeddings',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.String(255), nullable=False),
        sa.Column('data_key', sa.String(255), nullable=False),
        sa.Column('node_key', sa.String(255), nullable=False),
        sa.Column('node_path', sa.String(1024), nullable=True),
        sa.Column('node_type', sa.String(50), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('data', postgresql.JSONB, nullable=False),
        sa.Column('data_original', sa.Text, nullable=True),
        sa.Column('data_format', sa.String(50), nullable=True),
        sa.Column('embedding', sa.LargeBinary, nullable=False),  # Will be altered to vector type
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Alter column to use vector type
    op.execute('ALTER TABLE embeddings ALTER COLUMN embedding TYPE vector(384) USING embedding::vector(384)')
    op.create_index('idx_embeddings_project', 'embeddings', ['project_id'])
    op.create_index('idx_embeddings_project_node_key', 'embeddings', ['project_id', 'node_key'], unique=True)
    op.create_index('idx_embeddings_project_data_key', 'embeddings', ['project_id', 'data_key'])
    # Create HNSW index for vector similarity search
    op.execute('CREATE INDEX idx_embeddings_vector ON embeddings USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)')

    # Audit events table
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

    # Webhook endpoints table
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

    # Webhook deliveries table
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

    # Rate limit entries table
    op.create_table(
        'rate_limit_entries',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('rate_key', sa.String(512), nullable=False),
        sa.Column('request_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_rate_limit_key_time', 'rate_limit_entries', ['rate_key', 'request_time'])

    # Agent registrations table
    op.create_table(
        'agent_registrations',
        sa.Column('agent_id', sa.String(255), primary_key=True),
        sa.Column('project_id', sa.String(255), nullable=False),
        sa.Column('tenant_id', sa.String(255), sa.ForeignKey('tenants.tenant_id', ondelete='CASCADE'), nullable=True),
        sa.Column('needs', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
        sa.Column('notification_method', sa.String(20), nullable=False, server_default='redis'),
        sa.Column('response_format', sa.String(20), nullable=False, server_default='json'),
        sa.Column('notification_channel', sa.String(255), nullable=True),
        sa.Column('webhook_url', sa.Text, nullable=True),
        sa.Column('webhook_secret', sa.Text, nullable=True),
        sa.Column('data_keys', postgresql.ARRAY(sa.Text), nullable=False, server_default='{}'),
        sa.Column('last_sequence', sa.String(255), nullable=True),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True),
        sa.Column('data', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('idx_agent_project', 'agent_registrations', ['project_id'])
    op.create_index('idx_agent_tenant', 'agent_registrations', ['tenant_id'])
    op.create_index('idx_agent_last_seen', 'agent_registrations', ['last_seen'])


def downgrade() -> None:
    op.drop_table('agent_registrations')
    op.drop_table('rate_limit_entries')
    op.drop_table('webhook_deliveries')
    op.drop_table('webhook_endpoints')
    op.drop_table('audit_events')
    op.drop_table('embeddings')
    op.drop_table('snapshots')
    op.drop_table('events')
    op.drop_table('service_account_keys')
    op.drop_table('service_accounts')
    op.drop_table('api_key_roles')
    op.drop_table('api_keys')
    op.drop_table('tenant_projects')
    op.drop_table('tenant_usage')
    op.drop_table('tenants')
    op.execute('DROP EXTENSION IF EXISTS "vector"')
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
