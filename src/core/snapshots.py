"""
Event sourcing snapshot system.

Provides snapshots of project state at specific sequence numbers to:
- Avoid race conditions in agent registration
- Enable efficient state reconstruction
- Support time-travel queries
- Provide backup/restore capabilities
"""

import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from redis.asyncio import Redis
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Snapshot:
    """Snapshot of project state at a specific sequence"""
    project_id: str
    sequence: str
    timestamp: float
    data: Dict[str, Any]  # Full project state (all data keys)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "data": self.data,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Snapshot":
        return cls(
            project_id=data["project_id"],
            sequence=data["sequence"],
            timestamp=data["timestamp"],
            data=data["data"],
            metadata=data.get("metadata", {}),
        )


class SnapshotManager:
    """
    Manages event sourcing snapshots.

    Storage structure in Redis:
    - contex:snapshot:{project_id}:latest -> sequence of latest snapshot
    - contex:snapshot:{project_id}:{sequence} -> snapshot data
    - contex:snapshots:{project_id} -> sorted set of (sequence, timestamp)
    """

    def __init__(self, redis: Redis, max_snapshots: int = 10):
        self.redis = redis
        self.max_snapshots = max_snapshots
        self.snapshot_prefix = "contex:snapshot:"
        self.snapshots_set_prefix = "contex:snapshots:"

    async def create_snapshot(
        self,
        project_id: str,
        sequence: str,
        data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Snapshot:
        """
        Create a snapshot of project state.

        Args:
            project_id: Project identifier
            sequence: Event sequence number
            data: Full project state (all data keys)
            metadata: Optional metadata about the snapshot

        Returns:
            Created snapshot
        """
        snapshot = Snapshot(
            project_id=project_id,
            sequence=sequence,
            timestamp=time.time(),
            data=data,
            metadata=metadata or {},
        )

        # Store snapshot
        snapshot_key = f"{self.snapshot_prefix}{project_id}:{sequence}"
        await self.redis.set(snapshot_key, json.dumps(snapshot.to_dict()))

        # Update latest snapshot pointer
        latest_key = f"{self.snapshot_prefix}{project_id}:latest"
        await self.redis.set(latest_key, sequence)

        # Add to sorted set (for listing and retention)
        snapshots_set = f"{self.snapshots_set_prefix}{project_id}"
        await self.redis.zadd(snapshots_set, {sequence: snapshot.timestamp})

        # Enforce retention policy
        await self._enforce_retention(project_id)

        logger.info("Snapshot created",
                   project_id=project_id,
                   sequence=sequence,
                   data_keys=len(data))

        return snapshot

    async def get_snapshot(self, project_id: str, sequence: str) -> Optional[Snapshot]:
        """
        Get a specific snapshot.

        Args:
            project_id: Project identifier
            sequence: Snapshot sequence number

        Returns:
            Snapshot or None if not found
        """
        snapshot_key = f"{self.snapshot_prefix}{project_id}:{sequence}"
        data = await self.redis.get(snapshot_key)

        if not data:
            return None

        return Snapshot.from_dict(json.loads(data))

    async def get_latest_snapshot(self, project_id: str) -> Optional[Snapshot]:
        """
        Get the most recent snapshot for a project.

        Args:
            project_id: Project identifier

        Returns:
            Latest snapshot or None
        """
        latest_key = f"{self.snapshot_prefix}{project_id}:latest"
        sequence = await self.redis.get(latest_key)

        if not sequence:
            return None

        if isinstance(sequence, bytes):
            sequence = sequence.decode()

        return await self.get_snapshot(project_id, sequence)

    async def get_closest_snapshot(
        self,
        project_id: str,
        target_sequence: str,
    ) -> Optional[Snapshot]:
        """
        Get the snapshot closest to (but not after) target sequence.

        Args:
            project_id: Project identifier
            target_sequence: Target sequence number

        Returns:
            Closest snapshot before target, or None
        """
        snapshots_set = f"{self.snapshots_set_prefix}{project_id}"

        # Get all snapshots up to target
        # Assuming sequences are lexicographically ordered (timestamps)
        snapshots = await self.redis.zrangebyscore(
            snapshots_set,
            "-inf",
            "+inf",
            withscores=True
        )

        if not snapshots:
            return None

        # Find closest snapshot <= target_sequence
        closest_seq = None
        for seq, _ in snapshots:
            if isinstance(seq, bytes):
                seq = seq.decode()
            if seq <= target_sequence:
                closest_seq = seq
            else:
                break

        if closest_seq:
            return await self.get_snapshot(project_id, closest_seq)

        return None

    async def list_snapshots(self, project_id: str) -> List[Dict[str, Any]]:
        """
        List all snapshots for a project.

        Args:
            project_id: Project identifier

        Returns:
            List of snapshot metadata (sequence, timestamp)
        """
        snapshots_set = f"{self.snapshots_set_prefix}{project_id}"
        snapshots = await self.redis.zrevrange(
            snapshots_set,
            0,
            -1,
            withscores=True
        )

        result = []
        for seq, timestamp in snapshots:
            if isinstance(seq, bytes):
                seq = seq.decode()
            result.append({
                "sequence": seq,
                "timestamp": timestamp,
            })

        return result

    async def delete_snapshot(self, project_id: str, sequence: str):
        """
        Delete a specific snapshot.

        Args:
            project_id: Project identifier
            sequence: Snapshot sequence
        """
        snapshot_key = f"{self.snapshot_prefix}{project_id}:{sequence}"
        await self.redis.delete(snapshot_key)

        snapshots_set = f"{self.snapshots_set_prefix}{project_id}"
        await self.redis.zrem(snapshots_set, sequence)

        logger.info("Snapshot deleted", project_id=project_id, sequence=sequence)

    async def _enforce_retention(self, project_id: str):
        """
        Enforce snapshot retention policy (keep only last N snapshots).

        Args:
            project_id: Project identifier
        """
        snapshots_set = f"{self.snapshots_set_prefix}{project_id}"
        count = await self.redis.zcard(snapshots_set)

        if count > self.max_snapshots:
            # Remove oldest snapshots
            to_remove = count - self.max_snapshots
            old_snapshots = await self.redis.zrange(snapshots_set, 0, to_remove - 1)

            for seq in old_snapshots:
                if isinstance(seq, bytes):
                    seq = seq.decode()
                await self.delete_snapshot(project_id, seq)

            logger.info("Snapshot retention enforced",
                       project_id=project_id,
                       removed=to_remove,
                       kept=self.max_snapshots)

    async def create_snapshot_from_events(
        self,
        project_id: str,
        event_store,
        target_sequence: Optional[str] = None,
    ) -> Snapshot:
        """
        Create snapshot by replaying events.

        Args:
            project_id: Project identifier
            event_store: EventStore instance
            target_sequence: Sequence to snapshot at (None = latest)

        Returns:
            Created snapshot
        """
        # Get events
        if target_sequence:
            # Get events up to target_sequence
            events = await event_store.get_events(project_id, since="0", count=10000)
            # Filter to events <= target_sequence
            events = [e for e in events if e.get("id", "") <= target_sequence]
            sequence = target_sequence
        else:
            # Get all events
            events = await event_store.get_events(project_id, since="0", count=10000)
            sequence = events[-1].get("id") if events else "0"

        # Reconstruct state from events
        project_state = {}
        for event in events:
            event_data = event.get("data", {})
            if isinstance(event_data, str):
                try:
                    event_data = json.loads(event_data)
                except:
                    continue

            data_key = event_data.get("data_key")
            if data_key:
                project_state[data_key] = event_data.get("data")

        # Create snapshot
        return await self.create_snapshot(
            project_id=project_id,
            sequence=sequence,
            data=project_state,
            metadata={
                "events_replayed": len(events),
                "data_keys": len(project_state),
            },
        )


# Global instance
_snapshot_manager: Optional[SnapshotManager] = None


def init_snapshot_manager(redis: Redis, max_snapshots: int = 10) -> SnapshotManager:
    """Initialize global snapshot manager"""
    global _snapshot_manager
    _snapshot_manager = SnapshotManager(redis, max_snapshots=max_snapshots)
    return _snapshot_manager


def get_snapshot_manager() -> Optional[SnapshotManager]:
    """Get global snapshot manager instance"""
    return _snapshot_manager
