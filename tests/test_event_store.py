"""Tests for event store with PostgreSQL"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select
from src.core.event_store import EventStore
from src.core.db_models import Event, Tenant


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
        session.add(Tenant(tenant_id="tenant-1", name="Test Tenant", settings={}, metadata_={}))
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
async def test_append_event_with_long_data_key(db):
    data_key = "repo:" + "/".join(["snapshots"] * 30) + "/stack.json"
    sequence = await EventStore(db).append_event(
        project_id="p_long", event_type=f"{data_key}_updated", data={data_key: {}},
        data_key=data_key,
    )
    assert sequence is not None
