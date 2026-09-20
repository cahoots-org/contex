import pytest

from connectors.base import ChangeEvent, run


class FakePublisher:
    """Records each batch and reports every item as published."""

    def __init__(self):
        self.batches = []

    async def publish_batch(self, items):
        self.batches.append(items)
        return len(items)


def _events(n):
    return [ChangeEvent(op="upsert", key=f"k{i}", payload={"i": i}) for i in range(n)]


@pytest.mark.asyncio
async def test_batches_by_size():
    publisher = FakePublisher()
    stats = await run(_events(5), publisher, batch_size=2)
    assert [len(b) for b in publisher.batches] == [2, 2, 1]
    assert stats.published == 5
    assert stats.batches == 3


@pytest.mark.asyncio
async def test_maps_events_to_items():
    publisher = FakePublisher()
    await run(_events(1), publisher, batch_size=10)
    assert publisher.batches[0] == [{"data_key": "k0", "data": {"i": 0}, "data_format": "json"}]


@pytest.mark.asyncio
async def test_empty_stream_publishes_nothing():
    publisher = FakePublisher()
    stats = await run([], publisher, batch_size=2)
    assert publisher.batches == []
    assert stats.published == 0
    assert stats.batches == 0


@pytest.mark.asyncio
async def test_progress_called_per_batch():
    publisher = FakePublisher()
    seen = []
    await run(_events(5), publisher, batch_size=2, progress=seen.append)
    assert seen == [2, 4, 5]


@pytest.mark.asyncio
async def test_accepts_async_iterable():
    async def gen():
        for event in _events(3):
            yield event

    publisher = FakePublisher()
    stats = await run(gen(), publisher, batch_size=2)
    assert stats.published == 3
    assert [len(b) for b in publisher.batches] == [2, 1]


@pytest.mark.asyncio
async def test_rejects_non_change_event():
    publisher = FakePublisher()
    with pytest.raises(TypeError):
        await run([{"not": "an event"}], publisher)
