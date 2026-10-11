"""Bind unowned projects to the default tenant

MCP publishes did not record which tenant owns a project. Now that the
MCP tools check ownership, give every project that has data or
subscriptions but no owner to the default tenant, which is where its
events and subscriptions already sit.

Revision ID: 020
Revises: 019
Create Date: 2026-10-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '020'
down_revision: Union[str, None] = '019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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


def downgrade() -> None:
    pass
