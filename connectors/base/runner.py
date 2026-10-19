"""Batch a stream of ChangeEvents and publish it, once (`run`) or continuously (`stream`).

The runner is transport-agnostic: it takes any object with async
``publish_batch(items) -> published_count``, ``delete_batch(data_keys) ->
deleted_count`` and, to prune, ``list_keys() -> keys``. This keeps the
batching logic testable without a live server (see ContexPublisher for the MCP
transport).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from .change_event import ChangeEvent

log = logging.getLogger(__name__)

# Flush a publish batch before it would exceed this many bytes, keeping each
# request well under the server's upload cap (~50 MB) regardless of batch_size.
MAX_BATCH_BYTES = 40_000_000


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


def _item(event: ChangeEvent, secret_scanner, stats: RunStats):
    """The batch item for an upsert or delete, or None when the secret guard drops it."""
    if event.op == "delete":
        return event.key
    if secret_scanner is not None:
        why = secret_scanner.reason(event.key, event.payload, event.data_format)
        if why is not None:
            stats.skipped_secrets += 1
            log.warning("skip secret %s (%s)", event.key, why)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            return None
    return event.to_item()


async def run(
    events,
    publisher,
    batch_size: int = 500,
    progress: Callable[[int], None] | None = None,
    max_batch_bytes: int = MAX_BATCH_BYTES,
    secret_scanner=None,
    prune: bool = False,
) -> RunStats:
    """Accumulate ChangeEvents into batches and publish each.

    ``events`` is a sync or async iterable of :class:`ChangeEvent`. Upserts are
    published and deletes are sent through ``publisher.delete_batch``; a batch
    holds one op, so a change of op flushes and the stream's order is kept.
    ``progress`` is called with the running published-count after each flushed
    batch.

    A batch flushes when it reaches ``batch_size`` items or before its
    serialized size would exceed ``max_batch_bytes`` — so one oversized
    item can't push a request past the server's upload limit regardless of count.

    When ``secret_scanner`` is set, any event it flags is dropped before publish
    (counted in ``skipped_secrets``) so secrets never reach the store.

    With ``prune``, once the whole stream has been read, keys the publisher's
    origin holds that the stream neither upserted nor retained are deleted.
    Only prune a stream that reads the entire source.
    """
    stats = RunStats()
    buffer: list = []
    buffer_bytes = 0
    buffer_op = "upsert"
    seen: set[str] = set()

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
        if event.op not in ("upsert", "delete", "retain"):
            raise ValueError(f"unknown op {event.op!r} for {event.key}")
        if event.op == "retain":
            seen.add(event.key)
            continue
        if event.op != buffer_op:
            await flush()
            buffer_op = event.op
        item = _item(event, secret_scanner, stats)
        if item is None:
            continue
        if event.op == "upsert":
            seen.add(event.key)
        item_bytes = len(json.dumps(item, default=str))
        if buffer and buffer_bytes + item_bytes > max_batch_bytes:
            await flush()
        buffer.append(item)
        buffer_bytes += item_bytes
        if len(buffer) >= batch_size:
            await flush()
    await flush()
    # A run that read nothing is far likelier a credential or access failure than an empty source.
    if prune and (seen or stats.skipped_secrets):
        stale = sorted(set(await publisher.list_keys()) - seen)
        for start in range(0, len(stale), batch_size):
            stats.deleted += int(await publisher.delete_batch(stale[start:start + batch_size]))
        if stale:
            log.info("pruned %d keys gone from the source", len(stale))
    return stats


_DONE = object()


async def stream(
    events,
    publisher,
    ack,
    batch_size: int = 500,
    flush_interval: float = 1.0,
    max_batch_bytes: int = MAX_BATCH_BYTES,
    secret_scanner=None,
    progress: Callable[[int], None] | None = None,
) -> RunStats:
    """Publish an endless stream of ``(ChangeEvent | None, position)`` pairs.

    Batches as :func:`run` does, and also flushes ``flush_interval`` seconds
    after a batch's first event. Once a batch is accepted, ``await
    ack(positions)`` receives every position it consumed, in order, including
    events the secret guard dropped and ``None`` events (consumed, nothing to
    publish). A position is never acked before its event is published, so a
    crash replays at most the unacked tail. Returns when ``events`` ends.
    """
    stats = RunStats()
    buffer: list = []
    positions: list = []
    buffer_bytes = 0
    buffer_op = "upsert"
    deadline: float | None = None
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=batch_size)

    # Reading through a queue lets the timer fire without cancelling the
    # source's iterator, which would close an async generator for good.
    async def pump() -> None:
        try:
            async for pair in _aiter(events):
                await queue.put(pair)
        except Exception as exc:
            await queue.put(exc)
        else:
            await queue.put(_DONE)

    async def flush() -> None:
        nonlocal buffer_bytes, deadline
        if buffer:
            if buffer_op == "delete":
                stats.deleted += int(await publisher.delete_batch(list(buffer)))
            else:
                stats.published += int(await publisher.publish_batch(list(buffer)))
            stats.batches += 1
            buffer.clear()
            buffer_bytes = 0
            if progress is not None:
                progress(stats.published)
        if positions:
            await ack(list(positions))
            positions.clear()
        deadline = None

    reader = asyncio.create_task(pump())
    try:
        while True:
            timeout = None if deadline is None else max(0.0, deadline - loop.time())
            try:
                pair = await asyncio.wait_for(queue.get(), timeout)
            except TimeoutError:
                await flush()
                continue
            if pair is _DONE:
                break
            if isinstance(pair, Exception):
                raise pair
            event, position = pair
            item = None
            if event is not None:
                if not isinstance(event, ChangeEvent):
                    raise TypeError(f"expected ChangeEvent, got {type(event).__name__}")
                if event.op not in ("upsert", "delete"):
                    raise ValueError(f"a stream can't {event.op!r} {event.key}")
                if event.op != buffer_op:
                    await flush()
                    buffer_op = event.op
                item = _item(event, secret_scanner, stats)
            if item is not None:
                item_bytes = len(json.dumps(item, default=str))
                if buffer and buffer_bytes + item_bytes > max_batch_bytes:
                    await flush()
                buffer.append(item)
                buffer_bytes += item_bytes
            positions.append(position)
            if deadline is None:
                deadline = loop.time() + flush_interval
            if len(buffer) >= batch_size:
                await flush()
        await flush()
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
    return stats
