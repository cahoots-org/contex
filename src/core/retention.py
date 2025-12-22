"""Data retention policy management for Contex"""

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from src.core.database import DatabaseManager
from src.core.db_models import Event, Embedding, AgentRegistration
from src.core.logging import get_logger

logger = get_logger(__name__)


class RetentionManager:
    """
    Manages data retention policies for events, embeddings, and agent registrations.

    Features:
    - Configurable TTL for events
    - Event count limits to prevent unbounded growth
    - Automatic cleanup of stale data
    - Retention metrics
    """

    def __init__(
        self,
        db: DatabaseManager,
        events_ttl_days: int = 30,
        agent_inactive_days: int = 7,
        max_events_per_project: int = 10000,
    ):
        """
        Initialize retention manager.

        Args:
            db: Database manager
            events_ttl_days: Days to keep events (default: 30)
            agent_inactive_days: Days before cleaning up inactive agents (default: 7)
            max_events_per_project: Maximum events per project (default: 10000)
        """
        self.db = db
        self.events_ttl_days = events_ttl_days
        self.agent_inactive_days = agent_inactive_days
        self.max_events_per_project = max_events_per_project

        logger.info("Retention manager initialized",
                   events_ttl_days=events_ttl_days,
                   agent_inactive_days=agent_inactive_days,
                   max_events_per_project=max_events_per_project)

    async def cleanup_old_events(self, project_id: str) -> int:
        """
        Delete events older than events_ttl_days.

        Args:
            project_id: Project identifier

        Returns:
            Number of events deleted
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.events_ttl_days)

        try:
            async with self.db.session() as session:
                result = await session.execute(
                    delete(Event)
                    .where(Event.project_id == project_id)
                    .where(Event.created_at < cutoff)
                )
                deleted = result.rowcount

                if deleted > 0:
                    logger.info("Cleaned up old events",
                               project_id=project_id,
                               events_deleted=deleted,
                               cutoff_days=self.events_ttl_days)

                return deleted

        except Exception as e:
            logger.error("Failed to cleanup old events",
                        project_id=project_id,
                        error=str(e))
            return 0

    async def trim_events(self, project_id: str) -> int:
        """
        Trim events to max_events_per_project by deleting oldest.

        Args:
            project_id: Project identifier

        Returns:
            Number of events trimmed
        """
        try:
            async with self.db.session() as session:
                # Count total events
                result = await session.execute(
                    select(func.count(Event.id))
                    .where(Event.project_id == project_id)
                )
                total_count = result.scalar() or 0

                if total_count <= self.max_events_per_project:
                    return 0

                # Find the sequence number cutoff
                events_to_delete = total_count - self.max_events_per_project

                # Get sequence cutoff
                result = await session.execute(
                    select(Event.sequence)
                    .where(Event.project_id == project_id)
                    .order_by(Event.sequence.asc())
                    .offset(events_to_delete - 1)
                    .limit(1)
                )
                cutoff_sequence = result.scalar()

                if cutoff_sequence:
                    # Delete events below cutoff
                    result = await session.execute(
                        delete(Event)
                        .where(Event.project_id == project_id)
                        .where(Event.sequence <= cutoff_sequence)
                    )
                    deleted = result.rowcount

                    if deleted > 0:
                        logger.info("Trimmed events",
                                   project_id=project_id,
                                   events_trimmed=deleted,
                                   max_events=self.max_events_per_project)

                    return deleted

                return 0

        except Exception as e:
            logger.error("Failed to trim events",
                        project_id=project_id,
                        error=str(e))
            return 0

    async def cleanup_stale_agents(self) -> int:
        """
        Remove agent registrations that haven't been active for agent_inactive_days.

        Returns:
            Number of agents cleaned up
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.agent_inactive_days)

        try:
            async with self.db.session() as session:
                result = await session.execute(
                    delete(AgentRegistration)
                    .where(AgentRegistration.last_seen < cutoff)
                )
                deleted = result.rowcount

                if deleted > 0:
                    logger.info("Cleaned up stale agents",
                               agents_deleted=deleted,
                               inactive_days=self.agent_inactive_days)

                return deleted

        except Exception as e:
            logger.error("Failed to cleanup stale agents", error=str(e))
            return 0

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
            "events_cleaned_by_age": 0,
            "events_trimmed": 0,
        }

        # Cleanup old events by age
        stats["events_cleaned_by_age"] = await self.cleanup_old_events(project_id)

        # Trim events by count
        stats["events_trimmed"] = await self.trim_events(project_id)

        logger.info("Project cleanup complete", **stats)

        return stats

    async def cleanup_all_projects(self) -> dict:
        """
        Run cleanup for all projects.

        Returns:
            Dict with overall cleanup stats
        """
        logger.info("Running cleanup for all projects")

        # Find all unique project IDs
        async with self.db.session() as session:
            result = await session.execute(
                select(Event.project_id).distinct()
            )
            project_ids = [row[0] for row in result]

        stats = {
            "projects_processed": len(project_ids),
            "total_events_cleaned_by_age": 0,
            "total_events_trimmed": 0,
            "agents_cleaned": 0,
        }

        # Clean up each project
        for project_id in project_ids:
            project_stats = await self.cleanup_project(project_id)
            stats["total_events_cleaned_by_age"] += project_stats["events_cleaned_by_age"]
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
        stats = {
            "project_id": project_id,
            "event_count": 0,
            "oldest_event": None,
            "newest_event": None,
            "retention_config": {
                "events_ttl_days": self.events_ttl_days,
                "agent_inactive_days": self.agent_inactive_days,
                "max_events_per_project": self.max_events_per_project,
            },
        }

        try:
            async with self.db.session() as session:
                # Get event count
                result = await session.execute(
                    select(func.count(Event.id))
                    .where(Event.project_id == project_id)
                )
                stats["event_count"] = result.scalar() or 0

                # Get oldest event
                result = await session.execute(
                    select(Event.created_at)
                    .where(Event.project_id == project_id)
                    .order_by(Event.created_at.asc())
                    .limit(1)
                )
                oldest = result.scalar()
                if oldest:
                    stats["oldest_event"] = oldest.isoformat()

                # Get newest event
                result = await session.execute(
                    select(Event.created_at)
                    .where(Event.project_id == project_id)
                    .order_by(Event.created_at.desc())
                    .limit(1)
                )
                newest = result.scalar()
                if newest:
                    stats["newest_event"] = newest.isoformat()

        except Exception as e:
            logger.error("Failed to get retention stats",
                        project_id=project_id,
                        error=str(e))

        return stats


def get_retention_manager_from_env(db: DatabaseManager) -> RetentionManager:
    """
    Create RetentionManager from environment variables.

    Environment variables:
        RETENTION_EVENTS_TTL_DAYS: Days to keep events (default: 30)
        RETENTION_AGENT_INACTIVE_DAYS: Days before cleaning inactive agents (default: 7)
        RETENTION_MAX_EVENTS_PER_PROJECT: Max events per project (default: 10000)

    Args:
        db: Database manager

    Returns:
        RetentionManager instance
    """
    events_ttl_days = int(os.getenv("RETENTION_EVENTS_TTL_DAYS", "30"))
    agent_inactive_days = int(os.getenv("RETENTION_AGENT_INACTIVE_DAYS", "7"))
    max_events_per_project = int(os.getenv("RETENTION_MAX_EVENTS_PER_PROJECT", "10000"))

    return RetentionManager(
        db=db,
        events_ttl_days=events_ttl_days,
        agent_inactive_days=agent_inactive_days,
        max_events_per_project=max_events_per_project,
    )
