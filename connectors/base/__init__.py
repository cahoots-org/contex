"""Shared connector framework.

A connector is an out-of-process program that reads a source and emits a stream
of :class:`ChangeEvent`. The framework here turns that stream into published
context: :func:`run_connector` wires a :class:`ContexConfig` to a
:class:`ContexPublisher` and batches events through :func:`run`.
For continuous sources, :func:`run_stream_connector` opens a retrying publisher
and streams events through :func:`stream`.

Connectors depend only on this package plus their source's own client library.
"""
from __future__ import annotations

from collections.abc import Callable

from .change_event import ChangeEvent
from .documents import document_event, document_format, resolve_document_types
from .config import (
    ContexConfig,
    load_config,
    resolve_batch_size,
    resolve_flush_interval,
    resolve_flag,
    resolve_max_file_bytes,
    resolve_prune,
    resolve_secret_scanner,
)
from .globs import allowed, matches_any
from .publisher import ContexPublisher
from .runner import RunStats, run, stream

__all__ = [
    "ChangeEvent",
    "ContexConfig",
    "ContexPublisher",
    "RunStats",
    "allowed",
    "document_event",
    "document_format",
    "load_config",
    "matches_any",
    "resolve_batch_size",
    "resolve_document_types",
    "resolve_flush_interval",
    "resolve_flag",
    "resolve_max_file_bytes",
    "resolve_prune",
    "resolve_secret_scanner",
    "run",
    "run_connector",
    "run_stream_connector",
    "stream",
]


async def run_connector(
    config: ContexConfig,
    events,
    batch_size: int = 500,
    progress: Callable[[int], None] | None = None,
    secret_scanner=None,
    origin: str | None = None,
    prune: bool = False,
) -> RunStats:
    """Open a publisher for ``config`` and publish ``events`` through it.

    ``origin`` tags what this stream publishes, suffixed with the config's
    ``origin`` name when set. With ``prune``, keys that origin published before
    but this stream no longer yields are deleted.
    """
    if prune and origin is None:
        raise ValueError("prune needs an origin")
    if origin is not None and config.origin:
        origin = f"{origin}#{config.origin}"
    async with ContexPublisher(config, origin) as publisher:
        return await run(
            events,
            publisher,
            batch_size=batch_size,
            progress=progress,
            secret_scanner=secret_scanner,
            prune=prune,
        )


async def run_stream_connector(
    config: ContexConfig,
    events,
    ack,
    origin: str,
    batch_size: int = 500,
    flush_interval: float = 1.0,
    secret_scanner=None,
    progress: Callable[[int], None] | None = None,
) -> RunStats:
    """Open a publisher that rides out outages and stream ``events`` through it.

    ``ack`` receives the positions of each accepted batch (see :func:`stream`).
    ``origin`` is suffixed with the config's ``origin`` name when set.
    """
    if config.origin:
        origin = f"{origin}#{config.origin}"
    async with ContexPublisher(config, origin, retry_forever=True) as publisher:
        return await stream(
            events,
            publisher,
            ack,
            batch_size=batch_size,
            flush_interval=flush_interval,
            secret_scanner=secret_scanner,
            progress=progress,
        )
