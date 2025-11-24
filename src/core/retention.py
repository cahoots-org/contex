"""Data retention policy management for Contex"""

import os
from typing import Optional
from redis.asyncio import Redis
from .logging import get_logger

logger = get_logger(__name__)


class RetentionManager:
    """
    Manages data retention policies for events, embeddings, and agent registrations.

    Features:
    - Configurable TTL for events
    - Stream trimming to prevent unbounded growth
    - Automatic cleanup of stale data
    - Retention metrics
    """

    def __init__(
        self,
        redis: Redis,
        events_ttl_days: int = 30,
        agent_inactive_days: int = 7,
        max_stream_length: int = 10000,
    ):
        """
        Initialize retention manager.

        Args:
            redis: Redis connection
            events_ttl_days: Days to keep events (default: 30)
            agent_inactive_days: Days before cleaning up inactive agents (default: 7)
            max_stream_length: Maximum events per stream (default: 10000)
        """
        self.redis = redis
        self.events_ttl_seconds = events_ttl_days * 24 * 60 * 60
        self.agent_inactive_seconds = agent_inactive_days * 24 * 60 * 60
        self.max_stream_length = max_stream_length

        logger.info("Retention manager initialized",
                   events_ttl_days=events_ttl_days,
                   agent_inactive_days=agent_inactive_days,
                   max_stream_length=max_stream_length)

    async def apply_event_ttl(self, project_id: str) -> int:
        """
        Apply TTL to event stream for a project.

        Args:
            project_id: Project identifier

        Returns:
            Number of events affected
        """
        stream_key = f"events:{project_id}"

        try:
            # Set TTL on the stream key
            await self.redis.expire(stream_key, self.events_ttl_seconds)

            # Get current stream length
            stream_info = await self.redis.xinfo_stream(stream_key)
            length = stream_info.get("length", 0)

            logger.info("Applied TTL to event stream",
                       project_id=project_id,
                       ttl_seconds=self.events_ttl_seconds,
                       stream_length=length)

            return length
        except Exception as e:
            logger.error("Failed to apply event TTL",
                        project_id=project_id,
                        error=str(e))
            return 0

    async def trim_event_stream(self, project_id: str) -> int:
        """
        Trim event stream to max length using XTRIM.

        Args:
            project_id: Project identifier

        Returns:
            Number of events trimmed
        """
        stream_key = f"events:{project_id}"

        try:
            # Trim stream to max length (MAXLEN ~ approximate, more efficient)
            trimmed = await self.redis.xtrim(
                stream_key,
                maxlen=self.max_stream_length,
                approximate=True
            )

            if trimmed > 0:
                logger.info("Trimmed event stream",
                           project_id=project_id,
                           events_trimmed=trimmed,
                           max_length=self.max_stream_length)

            return trimmed
        except Exception as e:
            logger.error("Failed to trim event stream",
                        project_id=project_id,
                        error=str(e))
            return 0

    async def cleanup_stale_agents(self) -> int:
        """
        Remove agent registrations that haven't been active for agent_inactive_days.

        Returns:
            Number of agents cleaned up
        """
        import time
        current_time = time.time()
        cutoff_time = current_time - self.agent_inactive_seconds

        # Get all agent keys
        agent_keys = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="agent:*:last_seen", count=100)
            agent_keys.extend(keys)
            if cursor == 0:
                break

        cleaned = 0
        for key in agent_keys:
            try:
                last_seen_str = await self.redis.get(key)
                if last_seen_str:
                    # Decode bytes if necessary
                    if isinstance(last_seen_str, bytes):
                        last_seen_str = last_seen_str.decode()
                    last_seen = float(last_seen_str)
                    if last_seen < cutoff_time:
                        # Extract agent_id from key (agent:{agent_id}:last_seen)
                        agent_id = key.decode() if isinstance(key, bytes) else key
                        agent_id = agent_id.replace("agent:", "").replace(":last_seen", "")

                        # Delete agent data
                        await self.redis.delete(
                            f"agent:{agent_id}:last_seen",
                            f"agent:{agent_id}:data",
                            f"agent:{agent_id}:needs"
                        )

                        cleaned += 1
                        logger.info("Cleaned up stale agent",
                                   agent_id=agent_id,
                                   last_seen_days_ago=(current_time - last_seen) / 86400)
            except Exception as e:
                logger.error("Failed to process agent for cleanup",
                            key=str(key),
                            error=str(e))

        if cleaned > 0:
            logger.info("Stale agent cleanup complete",
                       agents_cleaned=cleaned)

        return cleaned

    async def cleanup_project(self, project_id: str) -> dict:
        """
        Run all cleanup operations for a project.

        Args:
            project_id: Project identifier

        Returns:
            Dict with cleanup stats
        """
        logger.info("Running project cleanup", project_id=project_id)

        stats = {
            "project_id": project_id,
            "events_ttl_applied": 0,
            "events_trimmed": 0,
        }

        # Apply TTL to events
        stats["events_ttl_applied"] = await self.apply_event_ttl(project_id)

        # Trim event stream
        stats["events_trimmed"] = await self.trim_event_stream(project_id)

        logger.info("Project cleanup complete", **stats)

        return stats

    async def cleanup_all_projects(self) -> dict:
        """
        Run cleanup for all projects.

        Returns:
            Dict with overall cleanup stats
        """
        logger.info("Running cleanup for all projects")

        # Find all event streams
        project_ids = set()
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="events:*", count=100)
            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                project_id = key_str.replace("events:", "")
                project_ids.add(project_id)
            if cursor == 0:
                break

        stats = {
            "projects_processed": len(project_ids),
            "total_events_ttl_applied": 0,
            "total_events_trimmed": 0,
            "agents_cleaned": 0,
        }

        # Clean up each project
        for project_id in project_ids:
            project_stats = await self.cleanup_project(project_id)
            stats["total_events_ttl_applied"] += project_stats["events_ttl_applied"]
            stats["total_events_trimmed"] += project_stats["events_trimmed"]

        # Clean up stale agents
        stats["agents_cleaned"] = await self.cleanup_stale_agents()

        logger.info("Global cleanup complete", **stats)

        return stats

    async def get_retention_stats(self, project_id: str) -> dict:
        """
        Get retention statistics for a project.

        Args:
            project_id: Project identifier

        Returns:
            Dict with retention stats
        """
        stream_key = f"events:{project_id}"

        stats = {
            "project_id": project_id,
            "event_stream_exists": False,
            "event_count": 0,
            "stream_ttl_seconds": None,
            "max_stream_length": self.max_stream_length,
        }

        try:
            # Check if stream exists
            stream_exists = await self.redis.exists(stream_key)
            stats["event_stream_exists"] = bool(stream_exists)

            if stream_exists:
                # Get stream info
                stream_info = await self.redis.xinfo_stream(stream_key)
                stats["event_count"] = stream_info.get("length", 0)

                # Get TTL
                ttl = await self.redis.ttl(stream_key)
                if ttl > 0:
                    stats["stream_ttl_seconds"] = ttl
        except Exception as e:
            logger.error("Failed to get retention stats",
                        project_id=project_id,
                        error=str(e))

        return stats


def get_retention_manager_from_env(redis: Redis) -> RetentionManager:
    """
    Create RetentionManager from environment variables.

    Environment variables:
        RETENTION_EVENTS_TTL_DAYS: Days to keep events (default: 30)
        RETENTION_AGENT_INACTIVE_DAYS: Days before cleaning inactive agents (default: 7)
        RETENTION_MAX_STREAM_LENGTH: Max events per stream (default: 10000)

    Args:
        redis: Redis connection

    Returns:
        RetentionManager instance
    """
    events_ttl_days = int(os.getenv("RETENTION_EVENTS_TTL_DAYS", "30"))
    agent_inactive_days = int(os.getenv("RETENTION_AGENT_INACTIVE_DAYS", "7"))
    max_stream_length = int(os.getenv("RETENTION_MAX_STREAM_LENGTH", "10000"))

    return RetentionManager(
        redis=redis,
        events_ttl_days=events_ttl_days,
        agent_inactive_days=agent_inactive_days,
        max_stream_length=max_stream_length,
    )
