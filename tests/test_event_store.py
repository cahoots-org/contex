"""Tests for event store with PostgreSQL"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from src.core.event_store import EventStore
from src.core.db_models import Event, Tenant
from src.core.models import DataPublishEvent


class TestEventStore:
    """Test EventStore functionality with PostgreSQL"""

    @pytest_asyncio.fixture
    async def event_store(self, db):
        """Create an EventStore instance"""
        return EventStore(db)

    @pytest.mark.asyncio
    async def test_append_event(self, event_store):
        """Test appending an event"""
        sequence = await event_store.append_event(
            project_id="proj1", event_type="test_event", data={"key": "value"}
        )

        assert sequence is not None
        assert isinstance(sequence, str)

    @pytest.mark.asyncio
    async def test_append_multiple_events(self, event_store):
        """Test appending multiple events"""
        seq1 = await event_store.append_event("proj1", "event1", {"data": 1})
        seq2 = await event_store.append_event("proj1", "event2", {"data": 2})

        assert seq1 != seq2
        # Sequences should be ordered (as integers in strings)
        assert int(seq1) < int(seq2)

    @pytest.mark.asyncio
    async def test_get_events_since_beginning(self, event_store):
        """Test getting all events from the beginning"""
        # Append events
        await event_store.append_event("proj1", "event1", {"data": 1})
        await event_store.append_event("proj1", "event2", {"data": 2})

        # Get all events
        events = await event_store.get_events_since("proj1", "0")

        assert len(events) == 2
        assert events[0]["event_type"] == "event1"
        assert events[1]["event_type"] == "event2"
        assert events[0]["data"]["data"] == 1
        assert events[1]["data"]["data"] == 2

    @pytest.mark.asyncio
    async def test_get_events_since_specific_sequence(self, event_store):
        """Test getting events since a specific sequence"""
        # Append events
        seq1 = await event_store.append_event("proj1", "event1", {"data": 1})
        await event_store.append_event("proj1", "event2", {"data": 2})
        await event_store.append_event("proj1", "event3", {"data": 3})

        # Get events after seq1
        events = await event_store.get_events_since("proj1", seq1)

        assert len(events) == 2
        assert events[0]["event_type"] == "event2"
        assert events[1]["event_type"] == "event3"

    @pytest.mark.asyncio
    async def test_get_events_with_count_limit(self, event_store):
        """Test getting events with count limit"""
        # Append many events
        for i in range(10):
            await event_store.append_event("proj1", f"event{i}", {"data": i})

        # Get only first 3
        events = await event_store.get_events_since("proj1", "0", count=3)

        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_get_all_events(self, event_store):
        """Test getting all events for a project"""
        # Append events
        await event_store.append_event("proj1", "event1", {"data": 1})
        await event_store.append_event("proj1", "event2", {"data": 2})

        # Get all events
        events = await event_store.get_all_events("proj1")

        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_get_all_events_with_count(self, event_store):
        """Test getting all events with count limit"""
        # Append events
        for i in range(5):
            await event_store.append_event("proj1", f"event{i}", {"data": i})

        # Get with limit
        events = await event_store.get_all_events("proj1", count=3)

        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_get_latest_sequence(self, event_store):
        """Test getting the latest sequence number"""
        # Initially no events
        latest = await event_store.get_latest_sequence("proj1")
        assert latest is None

        # Add events
        seq1 = await event_store.append_event("proj1", "event1", {"data": 1})
        seq2 = await event_store.append_event("proj1", "event2", {"data": 2})

        # Latest should be seq2
        latest = await event_store.get_latest_sequence("proj1")
        assert latest == seq2

    @pytest.mark.asyncio
    async def test_get_stream_length(self, event_store):
        """Test getting total number of events"""
        # Initially 0
        length = await event_store.get_stream_length("proj1")
        assert length == 0

        # Add events
        await event_store.append_event("proj1", "event1", {"data": 1})
        await event_store.append_event("proj1", "event2", {"data": 2})

        # Should be 2
        length = await event_store.get_stream_length("proj1")
        assert length == 2

    @pytest.mark.asyncio
    async def test_project_isolation(self, event_store):
        """Test that projects are isolated"""
        # Add events to different projects
        await event_store.append_event("proj1", "event1", {"data": 1})
        await event_store.append_event("proj2", "event2", {"data": 2})

        # Each project should only see its events
        proj1_events = await event_store.get_events_since("proj1", "0")
        proj2_events = await event_store.get_events_since("proj2", "0")

        assert len(proj1_events) == 1
        assert len(proj2_events) == 1
        assert proj1_events[0]["event_type"] == "event1"
        assert proj2_events[0]["event_type"] == "event2"

    @pytest.mark.asyncio
    async def test_delete_project_events(self, event_store):
        """Test deleting all events for a project"""
        # Add events
        await event_store.append_event("proj1", "event1", {"data": 1})
        await event_store.append_event("proj1", "event2", {"data": 2})

        # Verify events exist
        assert await event_store.get_stream_length("proj1") == 2

        # Delete
        await event_store.delete_project_events("proj1")

        # Verify deleted
        assert await event_store.get_stream_length("proj1") == 0

    @pytest.mark.asyncio
    async def test_event_data_integrity(self, event_store):
        """Test that complex data structures are preserved"""
        complex_data = {
            "nested": {"level2": {"level3": "value"}},
            "array": [1, 2, 3],
            "mixed": [{"a": 1}, {"b": 2}],
        }

        await event_store.append_event("proj1", "complex", complex_data)

        events = await event_store.get_events_since("proj1", "0")

        assert events[0]["data"] == complex_data

    @pytest.mark.asyncio
    async def test_empty_project(self, event_store):
        """Test querying a project with no events"""
        events = await event_store.get_events_since("nonexistent", "0")

        assert events == []

    @pytest.mark.asyncio
    async def test_sequence_format(self, event_store):
        """Test that sequence IDs are numeric strings (PostgreSQL sequences)"""
        seq = await event_store.append_event("proj1", "test", {})

        # PostgreSQL uses integer sequences
        assert seq.isdigit()
        assert int(seq) > 0


@pytest.mark.asyncio
async def test_append_event_persists_provenance(db):
    # Create tenant required by FK constraint
    async with db.session() as session:
        session.add(Tenant(tenant_id="tenant-1", name="Test Tenant", plan="free", quotas={}, settings={}, metadata_={}))
        await session.flush()

    store = EventStore(db)
    seq = await store.append_event(
        "proj-prov", "thing_updated", {"thing": 1},
        tenant_id="tenant-1",
        source="api",
        actor={"actor_id": "key-abc", "actor_type": "api_key", "actor_ip": "10.0.0.9"},
    )
    assert seq == "1"

    async with db.session() as session:
        row = (await session.execute(
            select(Event).where(Event.project_id == "proj-prov")
        )).scalar_one()
        assert row.source == "api"
        assert row.actor_id == "key-abc"
        assert row.actor_type == "api_key"
        assert row.actor_ip == "10.0.0.9"


@pytest.mark.asyncio
async def test_append_event_provenance_defaults(db):
    store = EventStore(db)
    await store.append_event("proj-prov2", "thing_updated", {"thing": 2})
    async with db.session() as session:
        row = (await session.execute(
            select(Event).where(Event.project_id == "proj-prov2")
        )).scalar_one()
        assert row.source == "api"      # default
        assert row.actor_id is None     # unauthenticated
        assert row.actor_type is None
        assert row.actor_ip is None
