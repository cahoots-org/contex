"""Add per-project event sequence counter for atomic sequence assignment

Revision ID: 006
Revises: 005
Create Date: 2026-09-04 00:00:00.000000

Fixes #104: sequence assignment previously used SELECT MAX(sequence)+1 then
INSERT with no locking, so concurrent publishes to the same project computed the
same sequence and the loser violated the (project_id, sequence) unique
constraint -> IntegrityError -> 500 (with the losing write lost).

This introduces a dedicated per-project counter table. The event store now bumps
the counter with a single atomic

    INSERT INTO event_sequence_counters (project_id, last_sequence)
    VALUES (:project_id, 1)
    ON CONFLICT (project_id)
    DO UPDATE SET last_sequence = event_sequence_counters.last_sequence + 1
    RETURNING last_sequence

statement, which takes a row lock on the project's counter row and serialises
concurrent publishes to that project while keeping sequences per-project,
monotonic, and starting from 1. Existing events are backfilled so new sequences
continue from each project's current max.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_sequence_counters",
        sa.Column("project_id", sa.String(length=255), primary_key=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # Backfill from any events that already exist so new sequences continue from
    # each project's current max rather than restarting at 1.
    op.execute(
        """
        INSERT INTO event_sequence_counters (project_id, last_sequence)
        SELECT project_id, MAX(sequence)
        FROM events
        GROUP BY project_id
        """
    )


def downgrade() -> None:
    op.drop_table("event_sequence_counters")
