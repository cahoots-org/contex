"""Batch a stream of ChangeEvents and publish it.

The runner is transport-agnostic: it takes any object with an async
``publish_batch(items) -> published_count``. This keeps the batching logic
testable without a live server (see ContexPublisher for the MCP transport).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .change_event import ChangeEvent


@dataclass
class RunStats:
    """What a run produced."""

    published: int = 0
    batches: int = 0


async def _aiter(events):
    """Iterate a sync or async iterable of ChangeEvents uniformly."""
    if hasattr(events, "__aiter__"):
        async for event in events:
            yield event
    else:
        for event in events:
            yield event


async def run(
    events,
    publisher,
    batch_size: int = 500,
    progress: Callable[[int], None] | None = None,
) -> RunStats:
    """Accumulate ChangeEvents into batches and publish each.

    ``events`` is a sync or async iterable of :class:`ChangeEvent`. ``publisher``
    exposes an async ``publish_batch(items) -> int``. ``progress`` is called with
    the running published-count after each flushed batch.
    """
    stats = RunStats()
    buffer: list[dict] = []

    async def flush() -> None:
        if not buffer:
            return
        published = await publisher.publish_batch(list(buffer))
        stats.published += int(published)
        stats.batches += 1
        buffer.clear()
        if progress is not None:
            progress(stats.published)

    async for event in _aiter(events):
        if not isinstance(event, ChangeEvent):
            raise TypeError(f"expected ChangeEvent, got {type(event).__name__}")
        buffer.append(event.to_item())
        if len(buffer) >= batch_size:
            await flush()
    await flush()
    return stats
