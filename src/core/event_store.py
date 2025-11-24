"""Event sourcing store using Redis Streams"""

import json
from typing import Dict, List, Any, Optional
from redis.asyncio import Redis


class EventStore:
    """
    Event sourcing store using Redis Streams.

    Features:
    - Append-only event log per project
    - Automatic sequence numbers
    - Query events since sequence (for agent catch-up)
    - Query full event history
    """

    def __init__(self, redis: Redis):
        self.redis = redis

    async def append_event(
        self, project_id: str, event_type: str, data: Dict[str, Any]
    ) -> str:
        """
        Append event to project stream.

        Args:
            project_id: Project identifier
            event_type: Event type (e.g., "tech_stack_updated")
            data: Event data

        Returns:
            Event ID (sequence number)
        """
        stream_key = f"project:{project_id}:events"

        # Append to stream
        event_id = await self.redis.xadd(
            stream_key, {"event_type": event_type, "data": json.dumps(data)}
        )

        # event_id format: "1234567890123-0"
        sequence = event_id.decode() if isinstance(event_id, bytes) else event_id

        print(
            f"[EventStore] Appended event: {project_id}:{event_type} (seq: {sequence})"
        )

        return sequence

    async def get_events_since(
        self, project_id: str, since_id: str = "0", count: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get events since a specific sequence number.

        Args:
            project_id: Project identifier
            since_id: Event ID to start from (exclusive)
            count: Maximum number of events to return

        Returns:
            List of events: [{"sequence": "...", "event_type": "...", "data": {...}}, ...]
        """
        stream_key = f"project:{project_id}:events"

        # Read from stream
        # xread returns: [[b'stream_key', [(b'event_id', {b'field': b'value'})]]]
        results = await self.redis.xread({stream_key: since_id}, count=count)

        events = []

        if results:
            for stream_name, stream_events in results:
                for event_id, event_data in stream_events:
                    # Decode
                    sequence = (
                        event_id.decode() if isinstance(event_id, bytes) else event_id
                    )

                    event_type_bytes = event_data.get(b"event_type") or event_data.get(
                        "event_type"
                    )
                    event_type = (
                        event_type_bytes.decode()
                        if isinstance(event_type_bytes, bytes)
                        else event_type_bytes
                    )

                    data_bytes = event_data.get(b"data") or event_data.get("data")
                    data_str = (
                        data_bytes.decode()
                        if isinstance(data_bytes, bytes)
                        else data_bytes
                    )
                    data = json.loads(data_str)

                    events.append(
                        {"sequence": sequence, "event_type": event_type, "data": data}
                    )

        print(
            f"[EventStore] Retrieved {len(events)} events since {since_id} for {project_id}"
        )

        return events

    async def get_all_events(
        self, project_id: str, count: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all events for a project.

        Args:
            project_id: Project identifier
            count: Maximum number of events (None = all)

        Returns:
            List of events
        """
        stream_key = f"project:{project_id}:events"

        # Read entire stream
        # xrange: start "-" (beginning), end "+" (end)
        results = await self.redis.xrange(stream_key, min="-", max="+", count=count)

        events = []

        for event_id, event_data in results:
            # Decode
            sequence = event_id.decode() if isinstance(event_id, bytes) else event_id

            event_type_bytes = event_data.get(b"event_type") or event_data.get(
                "event_type"
            )
            event_type = (
                event_type_bytes.decode()
                if isinstance(event_type_bytes, bytes)
                else event_type_bytes
            )

            data_bytes = event_data.get(b"data") or event_data.get("data")
            data_str = (
                data_bytes.decode() if isinstance(data_bytes, bytes) else data_bytes
            )
            data = json.loads(data_str)

            events.append(
                {"sequence": sequence, "event_type": event_type, "data": data}
            )

        print(f"[EventStore] Retrieved {len(events)} total events for {project_id}")

        return events

    async def get_latest_sequence(self, project_id: str) -> Optional[str]:
        """
        Get the latest event sequence number for a project.

        Args:
            project_id: Project identifier

        Returns:
            Latest sequence number or None if no events
        """
        stream_key = f"project:{project_id}:events"

        # Get last event
        results = await self.redis.xrevrange(stream_key, max="+", min="-", count=1)

        if results:
            event_id = results[0][0]
            sequence = event_id.decode() if isinstance(event_id, bytes) else event_id
            return sequence

        return None

    async def get_stream_length(self, project_id: str) -> int:
        """Get total number of events for a project"""
        stream_key = f"project:{project_id}:events"
        return await self.redis.xlen(stream_key)

    async def delete_project_events(self, project_id: str):
        """Delete all events for a project"""
        stream_key = f"project:{project_id}:events"
        await self.redis.delete(stream_key)
        print(f"[EventStore] Deleted all events for {project_id}")
