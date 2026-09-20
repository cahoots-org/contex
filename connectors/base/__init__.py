"""Shared connector framework.

A connector is an out-of-process program that reads a source and emits a stream
of :class:`ChangeEvent`. The framework here turns that stream into published
context: :func:`run_connector` wires a :class:`ContexConfig` to a
:class:`ContexPublisher` and batches events through :func:`run`.

Connectors depend only on this package plus their source's own client library.
"""
from __future__ import annotations

from collections.abc import Callable

from .change_event import ChangeEvent
from .config import ContexConfig, load_config, resolve_batch_size
from .globs import allowed, matches_any
from .publisher import ContexPublisher
from .runner import RunStats, run

__all__ = [
    "ChangeEvent",
    "ContexConfig",
    "ContexPublisher",
    "RunStats",
    "allowed",
    "load_config",
    "matches_any",
    "resolve_batch_size",
    "run",
    "run_connector",
]


async def run_connector(
    config: ContexConfig,
    events,
    batch_size: int = 500,
    progress: Callable[[int], None] | None = None,
) -> RunStats:
    """Open a publisher for ``config`` and publish ``events`` through it."""
    async with ContexPublisher(config) as publisher:
        return await run(events, publisher, batch_size=batch_size, progress=progress)
