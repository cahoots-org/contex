"""run() flushes on item count and on accumulated byte size."""
from __future__ import annotations

import asyncio
import json

from connectors.base import ChangeEvent
from connectors.base.runner import run


class _RecordingPublisher:
    """Captures each batch's item count and serialized size."""

    def __init__(self) -> None:
        self.batches: list[int] = []
        self.batch_bytes: list[int] = []

    async def publish_batch(self, items: list[dict]) -> int:
        self.batches.append(len(items))
        self.batch_bytes.append(len(json.dumps(items)))
        return len(items)


def _events(n: int, payload: str):
    return [ChangeEvent(op="upsert", key=f"k{i}", payload=payload, data_format="text") for i in range(n)]


def test_flushes_on_count_when_no_byte_cap():
    pub = _RecordingPublisher()
    asyncio.run(run(_events(5, "x"), pub, batch_size=2))
    assert pub.batches == [2, 2, 1]


def test_byte_cap_flushes_before_exceeding_limit():
    # ~1KB payloads, batch_size high so only the byte cap can trigger a flush.
    pub = _RecordingPublisher()
    big = "x" * 1024
    cap = 3000  # ~2 items/batch
    asyncio.run(run(_events(5, big), pub, batch_size=1000, max_batch_bytes=cap))
    assert sum(pub.batches) == 5          # everything published
    assert len(pub.batches) > 1           # split into multiple requests
    assert all(b <= cap for b in pub.batch_bytes)  # no request exceeds the cap
