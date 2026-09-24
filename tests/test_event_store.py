"""Tests for event store with PostgreSQL"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select
from src.core.event_store import EventStore
from src.core.db_models import Event, EventSequenceCounter, Tenant
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
async def test_concurrent_appends_are_unique_and_monotonic(db):
    """Concurrent publishes to the same project must not race on the sequence.

    Regression for #104: the previous SELECT MAX(sequence)+1 then INSERT pattern
    let two concurrent transactions compute the same sequence, so the second
    INSERT violated the (project_id, sequence) unique constraint and 500'd, with
    the losing write lost. Fire many appends concurrently and assert every one
    succeeds, sequences are unique, and they form the contiguous run 1..N.
    """
    store = EventStore(db)
    n = 50

    # No append may raise: gather propagates the first IntegrityError (the 500 the
    # racy MAX+1/INSERT pattern produced). A single burst can miss the race window,
    # so run a few rounds to make the assertion reliable.
    total = 0
    for _ in range(3):
        results = await asyncio.gather(
            *(
                store.append_event("proj-race", f"event{i}", {"i": i})
                for i in range(n)
            )
        )
        assert len(results) == n
        total += n

    # Everything that landed in the table forms the contiguous, monotonic run
    # 1..total (per-project, from 1) with no duplicates and no gaps.
    async with db.session() as session:
        rows = (
            await session.execute(
                select(Event.sequence)
                .where(Event.project_id == "proj-race")
                .order_by(Event.sequence.asc())
            )
        ).scalars().all()
    assert rows == list(range(1, total + 1))


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


@pytest.mark.asyncio
async def test_get_events_for_key_filters_by_key(db):
    """Only events matching the requested data_key are returned."""
    store = EventStore(db)
    await store.append_event("proj-key", "tech_stack_updated", {"tech_stack": 1}, data_key="tech_stack")
    await store.append_event("proj-key", "config_updated", {"config": "a"}, data_key="config")
    await store.append_event("proj-key", "tech_stack_updated", {"tech_stack": 2}, data_key="tech_stack")

    events = await store.get_events_for_key("proj-key", "tech_stack")

    assert len(events) == 2
    assert {e["data"]["tech_stack"] for e in events} == {1, 2}


@pytest.mark.asyncio
async def test_get_events_for_key_newest_first(db):
    """Events are ordered by sequence descending."""
    store = EventStore(db)
    seq1 = await store.append_event("proj-order", "k_updated", {"k": 1}, data_key="k")
    seq2 = await store.append_event("proj-order", "k_updated", {"k": 2}, data_key="k")
    seq3 = await store.append_event("proj-order", "k_updated", {"k": 3}, data_key="k")

    events = await store.get_events_for_key("proj-order", "k")

    assert [e["sequence"] for e in events] == [seq3, seq2, seq1]


@pytest.mark.asyncio
async def test_get_events_for_key_pagination(db):
    """limit and offset page through matching events, newest first."""
    store = EventStore(db)
    for i in range(5):
        await store.append_event("proj-page", "k_updated", {"k": i}, data_key="k")

    page1 = await store.get_events_for_key("proj-page", "k", limit=2, offset=0)
    page2 = await store.get_events_for_key("proj-page", "k", limit=2, offset=2)

    assert [e["data"]["k"] for e in page1] == [4, 3]
    assert [e["data"]["k"] for e in page2] == [2, 1]


@pytest.mark.asyncio
async def test_get_events_for_key_not_truncated_beyond_10k(db):
    """A key with >10k events returns correct, non-truncated newest-first history.

    Regression for #120: the old path loaded the whole project log capped at
    10k and filtered in Python, silently truncating. The key-scoped query must
    reach the newest events no matter how many total events precede them.
    """
    store = EventStore(db)
    async with db.session() as session:
        session.add(EventSequenceCounter(project_id="proj-big", last_sequence=10_005))
        session.add_all(
            Event(
                project_id="proj-big",
                event_type="k_updated",
                data={"k": i},
                data_key="k",
                sequence=i,
            )
            for i in range(1, 10_006)
        )
        await session.commit()

    newest = await store.get_events_for_key("proj-big", "k", limit=3)

    assert [e["sequence"] for e in newest] == ["10005", "10004", "10003"]
    assert [e["data"]["k"] for e in newest] == [10005, 10004, 10003]
