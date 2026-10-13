"""Unit tests for the streaming runner."""
from __future__ import annotations

import asyncio

import pytest

from connectors.base import ChangeEvent
from connectors.base.runner import stream


class FakePublisher:
    def __init__(self):
        self.batches = []

    async def publish_batch(self, items):
        self.batches.append(items)
        return len(items)

    async def delete_batch(self, data_keys):
        self.batches.append(("delete", data_keys))
        return len(data_keys)


class Acks:
    def __init__(self):
        self.calls = []

    async def __call__(self, positions):
        self.calls.append(positions)


def _up(key):
    return ChangeEvent(op="upsert", key=key, payload={"k": key})


async def _then_idle(pairs):
    for pair in pairs:
        yield pair
    await asyncio.Event().wait()


async def _run_briefly(events, publisher, ack, seconds=0.2, **kwargs):
    task = asyncio.create_task(stream(events, publisher, ack, **kwargs))
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_lone_event_flushes_on_timer():
    publisher, ack = FakePublisher(), Acks()
    await _run_briefly(_then_idle([(_up("a"), 0)]), publisher, ack, flush_interval=0.05)
    assert publisher.batches == [[{"data_key": "a", "data": {"k": "a"}, "data_format": "json"}]]
    assert ack.calls == [[0]]


@pytest.mark.asyncio
async def test_size_flush_acks_each_batch_with_its_positions():
    publisher, ack = FakePublisher(), Acks()
    pairs = [(_up(f"k{i}"), i) for i in range(5)]
    stats = await stream(pairs, publisher, ack, batch_size=2, flush_interval=60)
    assert [len(b) for b in publisher.batches] == [2, 2, 1]
    assert ack.calls == [[0, 1], [2, 3], [4]]
    assert stats.published == 5


@pytest.mark.asyncio
async def test_failed_publish_is_not_acked():
    class Failing(FakePublisher):
        async def publish_batch(self, items):
            raise RuntimeError("contex down")

    ack = Acks()
    with pytest.raises(RuntimeError, match="contex down"):
        await stream([(_up("a"), 0)], Failing(), ack, flush_interval=60)
    assert ack.calls == []


@pytest.mark.asyncio
async def test_secret_dropped_events_still_advance():
    class Scanner:
        def reason(self, key, payload, data_format):
            return "looks like a key" if key == "s" else None

    publisher, ack = FakePublisher(), Acks()
    stats = await stream([(_up("s"), 0), (_up("a"), 1)], publisher, ack, secret_scanner=Scanner())
    assert publisher.batches == [[{"data_key": "a", "data": {"k": "a"}, "data_format": "json"}]]
    assert ack.calls == [[0, 1]]
    assert stats.skipped_secrets == 1


@pytest.mark.asyncio
async def test_skipped_events_ack_on_timer():
    publisher, ack = FakePublisher(), Acks()
    await _run_briefly(_then_idle([(None, 0), (None, 1)]), publisher, ack, flush_interval=0.05)
    assert publisher.batches == []
    assert ack.calls == [[0, 1]]


@pytest.mark.asyncio
async def test_op_changes_flush_in_order():
    publisher, ack = FakePublisher(), Acks()
    pairs = [(_up("a"), 0), (ChangeEvent(op="delete", key="b", payload=None), 1), (_up("c"), 2)]
    stats = await stream(pairs, publisher, ack, flush_interval=60)
    assert publisher.batches[0][0]["data_key"] == "a"
    assert publisher.batches[1] == ("delete", ["b"])
    assert publisher.batches[2][0]["data_key"] == "c"
    assert ack.calls == [[0], [1], [2]]
    assert stats.deleted == 1


@pytest.mark.asyncio
async def test_byte_cap_flush_does_not_ack_the_next_event():
    publisher, ack = FakePublisher(), Acks()
    pairs = [(_up("a"), 0), (_up("b"), 1)]
    await stream(pairs, publisher, ack, max_batch_bytes=80, flush_interval=60)
    assert ack.calls == [[0], [1]]


@pytest.mark.asyncio
async def test_retain_is_rejected():
    with pytest.raises(ValueError, match="retain"):
        await stream([(ChangeEvent(op="retain", key="a", payload=None), 0)], FakePublisher(), Acks())


@pytest.mark.asyncio
async def test_source_errors_propagate():
    async def broken():
        yield (_up("a"), 0)
        raise RuntimeError("broker gone")

    with pytest.raises(RuntimeError, match="broker gone"):
        await stream(broken(), FakePublisher(), Acks(), flush_interval=60)
