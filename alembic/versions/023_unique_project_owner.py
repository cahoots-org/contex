"""Give every project exactly one owner

A project has one owning tenant, so project_id becomes the primary key of
tenant_projects. Where a project has several owners, the earliest binding
(lowest created_at, then tenant_id) is kept. Projects with data or
subscriptions but no owner are bound to the default tenant.

Revision ID: 023
Revises: 022
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '023'
down_revision: Union[str, None] = '022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM tenant_projects tp
        USING tenant_projects earlier
        WHERE tp.project_id = earlier.project_id
          AND (earlier.created_at, earlier.tenant_id) < (tp.created_at, tp.tenant_id)
        """
    )
    op.drop_constraint('tenant_projects_pkey', 'tenant_projects', type_='primary')
    op.create_primary_key('tenant_projects_pkey', 'tenant_projects', ['project_id'])
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
        ON CONFLICT (project_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_constraint('tenant_projects_pkey', 'tenant_projects', type_='primary')
    op.create_primary_key('tenant_projects_pkey', 'tenant_projects', ['tenant_id', 'project_id'])
