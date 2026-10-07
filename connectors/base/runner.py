"""Batch a stream of ChangeEvents and publish it.

The runner is transport-agnostic: it takes any object with async
``publish_batch(items) -> published_count`` and ``delete_batch(data_keys) ->
deleted_count``. This keeps the batching logic testable without a live server
(see ContexPublisher for the MCP transport).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from .change_event import ChangeEvent

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    """What a run produced."""

    published: int = 0
    deleted: int = 0
    batches: int = 0
    skipped_secrets: int = 0


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
    max_batch_bytes: int | None = None,
    secret_scanner=None,
) -> RunStats:
    """Accumulate ChangeEvents into batches and publish each.

    ``events`` is a sync or async iterable of :class:`ChangeEvent`. Upserts are
    published and deletes are sent through ``publisher.delete_batch``; a batch
    holds one op, so a change of op flushes and the stream's order is kept.
    ``progress`` is called with the running published-count after each flushed
    batch.

    A batch flushes when it reaches ``batch_size`` items or, if ``max_batch_bytes``
    is set, before its serialized size would exceed that cap — so one oversized
    item can't push a request past the server's upload limit regardless of count.

    When ``secret_scanner`` is set, any event it flags is dropped before publish
    (counted in ``skipped_secrets``) so secrets never reach the store.
    """
    stats = RunStats()
    buffer: list = []
    buffer_bytes = 0
    buffer_op = "upsert"

    async def flush() -> None:
        nonlocal buffer_bytes
        if not buffer:
            return
        if buffer_op == "delete":
            stats.deleted += int(await publisher.delete_batch(list(buffer)))
        else:
            stats.published += int(await publisher.publish_batch(list(buffer)))
        stats.batches += 1
        buffer.clear()
        buffer_bytes = 0
        if progress is not None:
            progress(stats.published)

    async for event in _aiter(events):
        if not isinstance(event, ChangeEvent):
            raise TypeError(f"expected ChangeEvent, got {type(event).__name__}")
        if event.op not in ("upsert", "delete"):
            raise ValueError(f"unknown op {event.op!r} for {event.key}")
        if event.op != buffer_op:
            await flush()
            buffer_op = event.op
        if event.op == "delete":
            item = event.key
        else:
            if secret_scanner is not None:
                why = secret_scanner.reason(event.key, event.payload)
                if why is not None:
                    stats.skipped_secrets += 1
                    log.warning("skip secret %s (%s)", event.key, why)
                    continue
            item = event.to_item()
        item_bytes = len(json.dumps(item, default=str))
        if (
            buffer
            and max_batch_bytes is not None
            and buffer_bytes + item_bytes > max_batch_bytes
        ):
            await flush()
        buffer.append(item)
        buffer_bytes += item_bytes
        if len(buffer) >= batch_size:
            await flush()
    await flush()
    return stats
