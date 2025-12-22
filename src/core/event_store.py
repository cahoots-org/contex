"""Event sourcing store using PostgreSQL"""

from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DatabaseManager
from src.core.db_models import Event
from src.core.logging import get_logger

logger = get_logger(__name__)


class EventStore:
    """
    Event sourcing store using PostgreSQL.

    Features:
    - Append-only event log per project
    - Automatic sequence numbers (per-project)
    - Query events since sequence (for agent catch-up)
    - Query full event history
    """

    def __init__(self, db: DatabaseManager):
        self.db = db

    async def append_event(
        self,
        project_id: str,
        event_type: str,
        data: Dict[str, Any],
        tenant_id: Optional[str] = None,
    ) -> str:
        """
        Append event to project event log.

        Args:
            project_id: Project identifier
            event_type: Event type (e.g., "tech_stack_updated")
            data: Event data
            tenant_id: Optional tenant identifier

        Returns:
            Event sequence number as string (for compatibility with existing code)
        """
        async with self.db.session() as session:
            # Get next sequence number for this project
            result = await session.execute(
                select(func.coalesce(func.max(Event.sequence), 0) + 1)
                .where(Event.project_id == project_id)
            )
            sequence = result.scalar()

            # Create event
            event = Event(
                project_id=project_id,
                tenant_id=tenant_id,
                event_type=event_type,
                data=data,
                sequence=sequence,
            )
            session.add(event)
            await session.flush()

            # Return sequence as string for compatibility
            sequence_str = str(sequence)
            logger.debug(
                "Appended event",
                project_id=project_id,
                event_type=event_type,
                sequence=sequence_str,
            )

            return sequence_str

    async def get_events_since(
        self,
        project_id: str,
        since_id: str = "0",
        count: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get events since a specific sequence number.

        Args:
            project_id: Project identifier
            since_id: Sequence number to start from (exclusive)
            count: Maximum number of events to return

        Returns:
            List of events: [{"sequence": "...", "event_type": "...", "data": {...}}, ...]
        """
        # Parse since_id - handle both old Redis format ("timestamp-seq") and new format
        try:
            if "-" in since_id:
                # Old Redis Streams format, extract just the sequence part
                since_sequence = 0
            else:
                since_sequence = int(since_id) if since_id else 0
        except (ValueError, TypeError):
            since_sequence = 0

        async with self.db.session() as session:
            result = await session.execute(
                select(Event)
                .where(Event.project_id == project_id)
                .where(Event.sequence > since_sequence)
                .order_by(Event.sequence.asc())
                .limit(count)
            )
            events = result.scalars().all()

            event_list = [
                {
                    "sequence": str(e.sequence),
                    "event_type": e.event_type,
                    "data": e.data,
                }
                for e in events
            ]

            logger.debug(
                "Retrieved events since sequence",
                project_id=project_id,
                since_id=since_id,
                count=len(event_list),
            )

            return event_list

    async def get_all_events(
        self,
        project_id: str,
        count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all events for a project.

        Args:
            project_id: Project identifier
            count: Maximum number of events (None = all)

        Returns:
            List of events
        """
        async with self.db.session() as session:
            query = (
                select(Event)
                .where(Event.project_id == project_id)
                .order_by(Event.sequence.asc())
            )
            if count:
                query = query.limit(count)

            result = await session.execute(query)
            events = result.scalars().all()

            event_list = [
                {
                    "sequence": str(e.sequence),
                    "event_type": e.event_type,
                    "data": e.data,
                }
                for e in events
            ]

            logger.debug(
                "Retrieved all events",
                project_id=project_id,
                count=len(event_list),
            )

            return event_list

    async def get_latest_sequence(self, project_id: str) -> Optional[str]:
        """
        Get the latest event sequence number for a project.

        Args:
            project_id: Project identifier

        Returns:
            Latest sequence number as string, or None if no events
        """
        async with self.db.session() as session:
            result = await session.execute(
                select(func.max(Event.sequence))
                .where(Event.project_id == project_id)
            )
            sequence = result.scalar()

            if sequence is not None:
                return str(sequence)
            return None

    async def get_stream_length(self, project_id: str) -> int:
        """Get total number of events for a project."""
        async with self.db.session() as session:
            result = await session.execute(
                select(func.count(Event.id))
                .where(Event.project_id == project_id)
            )
            return result.scalar() or 0

    async def delete_project_events(self, project_id: str) -> int:
        """
        Delete all events for a project.

        Args:
            project_id: Project identifier

        Returns:
            Number of deleted events
        """
        async with self.db.session() as session:
            result = await session.execute(
                delete(Event).where(Event.project_id == project_id)
            )
            deleted_count = result.rowcount

            logger.info(
                "Deleted project events",
                project_id=project_id,
                deleted_count=deleted_count,
            )

            return deleted_count

    async def get_events_by_type(
        self,
        project_id: str,
        event_type: str,
        count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get events of a specific type for a project.

        Args:
            project_id: Project identifier
            event_type: Event type to filter by
            count: Maximum number of events (None = all)

        Returns:
            List of events
        """
        async with self.db.session() as session:
            query = (
                select(Event)
                .where(Event.project_id == project_id)
                .where(Event.event_type == event_type)
                .order_by(Event.sequence.asc())
            )
            if count:
                query = query.limit(count)

            result = await session.execute(query)
            events = result.scalars().all()

            return [
                {
                    "sequence": str(e.sequence),
                    "event_type": e.event_type,
                    "data": e.data,
                }
                for e in events
            ]

    async def get_event_count_by_tenant(self, tenant_id: str) -> int:
        """Get total number of events for a tenant."""
        async with self.db.session() as session:
            result = await session.execute(
                select(func.count(Event.id))
                .where(Event.tenant_id == tenant_id)
            )
            return result.scalar() or 0
