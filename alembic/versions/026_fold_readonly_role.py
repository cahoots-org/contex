"""Fold the readonly role into consumer

The two roles granted the same permissions. Keys with the readonly role
become consumers, which is also the new default role.

Revision ID: 026
Revises: 025
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '026'
down_revision: Union[str, None] = '025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE api_key_roles SET role = 'consumer' WHERE role = 'readonly'")
    op.alter_column('api_key_roles', 'role', server_default='consumer')


def downgrade() -> None:
    op.alter_column('api_key_roles', 'role', server_default='readonly')
