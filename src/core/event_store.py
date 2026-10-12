"""Event sourcing store using PostgreSQL"""

from typing import Any, Dict, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.database import DatabaseManager
from src.core.db_models import Event, EventSequenceCounter
from src.core.logging import get_logger

logger = get_logger(__name__)


class EventStore:
    """Append-only event log per project, with per-project sequence numbers."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    async def append_event(
        self,
        project_id: str,
        event_type: str,
        data: Dict[str, Any],
        tenant_id: Optional[str] = None,
        *,
        data_key: Optional[str] = None,
        source: str = "api",
        actor: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Append event to project event log.

        Args:
            project_id: Project identifier
            event_type: Event type (e.g., "tech_stack_updated")
            data: Event data
            tenant_id: Optional tenant identifier
            data_key: Data key this event concerns; denormalized for key-scoped queries
            source: Who/what produced the event (default: "api")
            actor: Optional dict with actor_id, actor_type, actor_ip

        Returns:
            Event sequence number as string (for compatibility with existing code)
        """
        actor = actor or {}
        async with self.db.session() as session:
            counter_stmt = (
                pg_insert(EventSequenceCounter)
                .values(project_id=project_id, last_sequence=1)
                .on_conflict_do_update(
                    index_elements=[EventSequenceCounter.project_id],
                    set_={
                        "last_sequence": EventSequenceCounter.last_sequence + 1,
                    },
                )
                .returning(EventSequenceCounter.last_sequence)
            )
            sequence = (await session.execute(counter_stmt)).scalar_one()

            event = Event(
                project_id=project_id,
                tenant_id=tenant_id,
                event_type=event_type,
                data=data,
                data_key=data_key,
                sequence=sequence,
                source=source,
                actor_id=actor.get("actor_id"),
                actor_type=actor.get("actor_type"),
                actor_ip=actor.get("actor_ip"),
            )
            session.add(event)
            await session.flush()

            sequence_str = str(sequence)
            logger.debug(
                "Appended event",
                project_id=project_id,
                event_type=event_type,
                sequence=sequence_str,
                source=source,
            )
            return sequence_str
