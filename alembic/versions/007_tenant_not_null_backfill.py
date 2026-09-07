"""Backfill tenant_id to default + NOT NULL (never-orphan)

Revision ID: 007
Revises: 006
Create Date: 2026-09-07 00:00:00.000000

Makes tenant ownership a total invariant for subscriptions, api_keys,
service_accounts, and events. Steps (order matters):

1. Ensure the default tenant row exists (fresh-DB safe).
2. Backfill NULL tenant_id values to 'default' in the four tables.
3. Backfill tenant_projects so every project_id from events/embeddings/
   subscriptions has a 'default' ownership row.
4. Set server_default='default' on the four tenant_id columns.
5. Set NOT NULL on the four tenant_id columns.
6. Add subscriptions.tenant_id -> tenants FK (was missing).
7. Recreate api_keys + service_accounts tenant FKs with ondelete=RESTRICT
   (were SET NULL).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '007'
down_revision: Union[str, None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Ensure default tenant exists (idempotent, fresh-DB safe).
    op.execute(
        """
        INSERT INTO tenants (tenant_id, name, plan, quotas, settings, metadata, is_active, created_at)
        VALUES (
            'default',
            'Default Tenant',
            'enterprise',
            '{}',
            '{"is_default": true}',
            '{}',
            true,
            now()
        )
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )

    # 2. Backfill NULLs in the four tenant-scoped tables.
    for table in ('subscriptions', 'api_keys', 'service_accounts', 'events'):
        op.execute(
            f"UPDATE {table} SET tenant_id = 'default' WHERE tenant_id IS NULL"
        )

    # 3. Backfill tenant_projects: every distinct project_id present in
    #    events ∪ embeddings ∪ subscriptions that has no ownership row yet.
    op.execute(
        """
        INSERT INTO tenant_projects (tenant_id, project_id, created_at)
        SELECT DISTINCT 'default', project_id, now()
        FROM (
            SELECT project_id FROM events
            UNION
            SELECT project_id FROM embeddings
            UNION
            SELECT project_id FROM subscriptions
        ) AS all_projects
        WHERE NOT EXISTS (
            SELECT 1 FROM tenant_projects tp
            WHERE tp.project_id = all_projects.project_id
        )
        ON CONFLICT DO NOTHING
        """
    )

    # 4. server_default='default' on the four columns.
    for table in ('subscriptions', 'api_keys', 'service_accounts', 'events'):
        op.alter_column(table, 'tenant_id', server_default='default')

    # 5. NOT NULL: 3 small tables via plain SET NOT NULL; events handled separately below.
    # events may be large: NOT VALID CHECK -> VALIDATE -> SET NOT NULL avoids a full-table ACCESS EXCLUSIVE scan.
    for table in ('subscriptions', 'api_keys', 'service_accounts'):
        op.alter_column(table, 'tenant_id', nullable=False)

    op.execute(
        "ALTER TABLE events ADD CONSTRAINT events_tenant_id_not_null "
        "CHECK (tenant_id IS NOT NULL) NOT VALID"
    )
    op.execute("ALTER TABLE events VALIDATE CONSTRAINT events_tenant_id_not_null")
    op.alter_column('events', 'tenant_id', nullable=False)
    op.execute("ALTER TABLE events DROP CONSTRAINT events_tenant_id_not_null")

    # 6. Add the missing subscriptions -> tenants FK.
    op.create_foreign_key(
        'fk_subscriptions_tenant',
        'subscriptions', 'tenants',
        ['tenant_id'], ['tenant_id'],
        ondelete='RESTRICT',
    )

    # 7. Recreate api_keys + service_accounts tenant FKs with ondelete=RESTRICT.
    op.drop_constraint('api_keys_tenant_id_fkey', 'api_keys', type_='foreignkey')
    op.create_foreign_key(
        'api_keys_tenant_id_fkey',
        'api_keys', 'tenants',
        ['tenant_id'], ['tenant_id'],
        ondelete='RESTRICT',
    )

    op.drop_constraint('service_accounts_tenant_id_fkey', 'service_accounts', type_='foreignkey')
    op.create_foreign_key(
        'service_accounts_tenant_id_fkey',
        'service_accounts', 'tenants',
        ['tenant_id'], ['tenant_id'],
        ondelete='RESTRICT',
    )


def downgrade() -> None:
    # Revert api_keys + service_accounts FKs back to SET NULL.
    op.drop_constraint('api_keys_tenant_id_fkey', 'api_keys', type_='foreignkey')
    op.create_foreign_key(
        'api_keys_tenant_id_fkey',
        'api_keys', 'tenants',
        ['tenant_id'], ['tenant_id'],
        ondelete='SET NULL',
    )

    op.drop_constraint('service_accounts_tenant_id_fkey', 'service_accounts', type_='foreignkey')
    op.create_foreign_key(
        'service_accounts_tenant_id_fkey',
        'service_accounts', 'tenants',
        ['tenant_id'], ['tenant_id'],
        ondelete='SET NULL',
    )

    # Drop the subscriptions FK.
    op.drop_constraint('fk_subscriptions_tenant', 'subscriptions', type_='foreignkey')

    # Re-add nullable, drop server_default; drop transient events CHECK if present.
    op.execute("ALTER TABLE events DROP CONSTRAINT IF EXISTS events_tenant_id_not_null")
    for table in ('subscriptions', 'api_keys', 'service_accounts', 'events'):
        op.alter_column(table, 'tenant_id', nullable=True, server_default=None)
