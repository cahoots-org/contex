"""Tests for data retention policies with PostgreSQL"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone

from src.core.retention import RetentionManager, get_retention_manager_from_env
from src.core.event_store import EventStore
from src.core.db_models import AgentRegistration


@pytest_asyncio.fixture
async def retention_manager(db):
    """Create RetentionManager for testing"""
    return RetentionManager(
        db=db,
        events_ttl_days=1,  # 1 day for testing
        agent_inactive_days=1,
        max_events_per_project=100,
    )


@pytest_asyncio.fixture
async def event_store(db):
    """Create EventStore for testing"""
    return EventStore(db)


class TestRetentionManager:
    """Test RetentionManager class"""

    @pytest.mark.asyncio
    async def test_initialization(self, db):
        """Test retention manager initialization"""
        manager = RetentionManager(
            db=db,
            events_ttl_days=30,
            agent_inactive_days=7,
            max_events_per_project=10000,
        )

        assert manager.events_ttl_days == 30
        assert manager.agent_inactive_days == 7
        assert manager.max_events_per_project == 10000

    @pytest.mark.asyncio
    async def test_cleanup_old_events(self, db, retention_manager, event_store):
        """Test cleaning up old events"""
        project_id = "test_project"

        # Add events (they will have current timestamp)
        for i in range(5):
            await event_store.append_event(project_id, f"event_{i}", {"data": i})

        # Verify events exist
        initial_count = await event_store.get_stream_length(project_id)
        assert initial_count == 5

        # Run cleanup - should not delete anything since events are new
        cleaned = await retention_manager.cleanup_old_events(project_id)
        assert cleaned == 0

        # Events should still be there
        final_count = await event_store.get_stream_length(project_id)
        assert final_count == 5

    @pytest.mark.asyncio
    async def test_trim_events(self, db, event_store):
        """Test trimming events to max count"""
        project_id = "trim_project"

        # Create manager with low max events
        manager = RetentionManager(
            db=db,
            events_ttl_days=30,
            agent_inactive_days=7,
            max_events_per_project=10,
        )

        # Add more events than max
        for i in range(25):
            await event_store.append_event(project_id, f"event_{i}", {"data": i})

        # Verify all events added
        initial_count = await event_store.get_stream_length(project_id)
        assert initial_count == 25

        # Trim events
        trimmed = await manager.trim_events(project_id)

        # Should have trimmed 15 events (25 - 10)
        assert trimmed == 15

        # Check remaining count
        final_count = await event_store.get_stream_length(project_id)
        assert final_count == 10

    @pytest.mark.asyncio
    async def test_trim_events_under_limit(self, db, event_store, retention_manager):
        """Test that events under limit are not trimmed"""
        project_id = "under_limit_project"

        # Add fewer events than max
        for i in range(50):
            await event_store.append_event(project_id, f"event_{i}", {"data": i})

        # Trim events (max is 100)
        trimmed = await retention_manager.trim_events(project_id)

        # Should not trim anything
        assert trimmed == 0

    @pytest.mark.asyncio
    async def test_cleanup_stale_agents(self, db, retention_manager):
        """Test cleaning up stale agent registrations"""
        # Add an active agent (last_seen = now)
        async with db.session() as session:
            active_agent = AgentRegistration(
                agent_id="active_agent",
                project_id="proj1",
                needs=["data"],
                last_seen=datetime.now(timezone.utc),
            )
            session.add(active_agent)

            # Add a stale agent (last_seen = 2 days ago)
            stale_agent = AgentRegistration(
                agent_id="stale_agent",
                project_id="proj1",
                needs=["data"],
                last_seen=datetime.now(timezone.utc) - timedelta(days=2),
            )
            session.add(stale_agent)

        # Clean up stale agents
        cleaned = await retention_manager.cleanup_stale_agents()

        # Should have cleaned up the stale agent
        assert cleaned == 1

        # Verify active agent still exists
        from sqlalchemy import select
        async with db.session() as session:
            result = await session.execute(
                select(AgentRegistration).where(
                    AgentRegistration.agent_id == "active_agent"
                )
            )
            active = result.scalar_one_or_none()
            assert active is not None

            result = await session.execute(
                select(AgentRegistration).where(
                    AgentRegistration.agent_id == "stale_agent"
                )
            )
            stale = result.scalar_one_or_none()
            assert stale is None

    @pytest.mark.asyncio
    async def test_cleanup_project(self, db, event_store):
        """Test cleanup for a specific project"""
        project_id = "cleanup_project"

        # Create manager with low max events
        manager = RetentionManager(
            db=db,
            events_ttl_days=30,
            agent_inactive_days=7,
            max_events_per_project=10,
        )

        # Add events
        for i in range(25):
            await event_store.append_event(project_id, f"event_{i}", {"data": i})

        # Run project cleanup
        stats = await manager.cleanup_project(project_id)

        # Verify stats
        assert stats["project_id"] == project_id
        assert stats["events_cleaned_by_age"] == 0  # Events are new
        assert stats["events_trimmed"] == 15  # 25 - 10

        # Verify event count
        final_count = await event_store.get_stream_length(project_id)
        assert final_count == 10

    @pytest.mark.asyncio
    async def test_cleanup_all_projects(self, db, event_store, retention_manager):
        """Test cleanup for all projects"""
        # Create events for multiple projects
        for proj_num in range(3):
            project_id = f"project_{proj_num}"
            for i in range(50):
                await event_store.append_event(project_id, f"event_{i}", {"data": i})

        # Add a stale agent
        async with db.session() as session:
            stale_agent = AgentRegistration(
                agent_id="global_stale_agent",
                project_id="proj1",
                needs=["data"],
                last_seen=datetime.now(timezone.utc) - timedelta(days=2),
            )
            session.add(stale_agent)

        # Run global cleanup
        stats = await retention_manager.cleanup_all_projects()

        # Verify stats
        assert stats["projects_processed"] == 3
        assert stats["agents_cleaned"] == 1

    @pytest.mark.asyncio
    async def test_get_retention_stats(self, db, event_store, retention_manager):
        """Test getting retention statistics"""
        project_id = "stats_project"

        # Add events
        for i in range(10):
            await event_store.append_event(project_id, f"event_{i}", {"data": i})

        # Get stats
        stats = await retention_manager.get_retention_stats(project_id)

        # Verify stats
        assert stats["project_id"] == project_id
        assert stats["event_count"] == 10
        assert stats["oldest_event"] is not None
        assert stats["newest_event"] is not None
        assert stats["retention_config"]["events_ttl_days"] == 1
        assert stats["retention_config"]["max_events_per_project"] == 100

    @pytest.mark.asyncio
    async def test_get_retention_stats_no_events(self, db, retention_manager):
        """Test getting stats for project with no events"""
        project_id = "empty_project"

        stats = await retention_manager.get_retention_stats(project_id)

        assert stats["project_id"] == project_id
        assert stats["event_count"] == 0
        assert stats["oldest_event"] is None
        assert stats["newest_event"] is None


class TestRetentionManagerFromEnv:
    """Test creating RetentionManager from environment variables"""

    @pytest.mark.asyncio
    async def test_get_from_env_defaults(self, db, monkeypatch):
        """Test creating manager with default environment values"""
        # Clear env vars to test defaults
        monkeypatch.delenv("RETENTION_EVENTS_TTL_DAYS", raising=False)
        monkeypatch.delenv("RETENTION_AGENT_INACTIVE_DAYS", raising=False)
        monkeypatch.delenv("RETENTION_MAX_EVENTS_PER_PROJECT", raising=False)

        manager = get_retention_manager_from_env(db)

        assert manager.events_ttl_days == 30
        assert manager.agent_inactive_days == 7
        assert manager.max_events_per_project == 10000

    @pytest.mark.asyncio
    async def test_get_from_env_custom(self, db, monkeypatch):
        """Test creating manager with custom environment values"""
        monkeypatch.setenv("RETENTION_EVENTS_TTL_DAYS", "60")
        monkeypatch.setenv("RETENTION_AGENT_INACTIVE_DAYS", "14")
        monkeypatch.setenv("RETENTION_MAX_EVENTS_PER_PROJECT", "5000")

        manager = get_retention_manager_from_env(db)

        assert manager.events_ttl_days == 60
        assert manager.agent_inactive_days == 14
        assert manager.max_events_per_project == 5000


class TestRetentionEdgeCases:
    """Test edge cases for retention"""

    @pytest.mark.asyncio
    async def test_cleanup_empty_project(self, db, retention_manager):
        """Test cleanup on project with no events"""
        stats = await retention_manager.cleanup_project("nonexistent_project")

        assert stats["events_cleaned_by_age"] == 0
        assert stats["events_trimmed"] == 0

    @pytest.mark.asyncio
    async def test_cleanup_no_stale_agents(self, db, retention_manager):
        """Test cleanup when no stale agents exist"""
        cleaned = await retention_manager.cleanup_stale_agents()

        assert cleaned == 0

    @pytest.mark.asyncio
    async def test_trim_nonexistent_project(self, db, retention_manager):
        """Test trimming non-existent project"""
        trimmed = await retention_manager.trim_events("nonexistent")

        assert trimmed == 0
