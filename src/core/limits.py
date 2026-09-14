"""Hard ceilings on request-sized inputs (defense in depth).

A single call must not be able to make Contex embed, scan, or buffer an
unbounded amount of work. These are safety ceilings, not tuning knobs; override
via env only to raise or lower a guardrail. Each cap has a sane default and a
positive-integer env override.
"""
from __future__ import annotations

import os


def _positive_int_env(name: str, default: int) -> int:
    """Read a positive-int env override, falling back to the default when the
    variable is unset, non-numeric, or non-positive."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Max results one query or subscription may request (caps the SQL LIMIT and the
# amount of match work per call).
MAX_TOP_K = _positive_int_env("MAX_TOP_K", 100)
# Max plain-English needs per subscription (each need is a separate embed + search).
MAX_NEEDS = _positive_int_env("MAX_NEEDS", 50)
# Max events one event-stream read may return.
MAX_EVENT_COUNT = _positive_int_env("MAX_EVENT_COUNT", 1000)
# Max items in one batch publish / register request.
MAX_BATCH_SIZE = _positive_int_env("MAX_BATCH_SIZE", 1000)


def clamp_top_k(top_k: int | None) -> int | None:
    """Clamp a requested result count to [1, MAX_TOP_K]. None passes through so
    the caller's own default still applies."""
    if top_k is None:
        return None
    return max(1, min(int(top_k), MAX_TOP_K))


def clamp_count(count: int | None) -> int:
    """Clamp an event-read count to [1, MAX_EVENT_COUNT]. None (unbounded)
    becomes MAX_EVENT_COUNT, so a read is never unlimited."""
    if count is None:
        return MAX_EVENT_COUNT
    return max(1, min(int(count), MAX_EVENT_COUNT))


def check_needs(needs) -> None:
    """Reject a subscription that declares more than MAX_NEEDS needs."""
    if len(needs) > MAX_NEEDS:
        raise ValueError(f"Too many needs: {len(needs)} (max {MAX_NEEDS})")


def check_batch_size(items, kind: str = "items") -> None:
    """Reject a batch request larger than MAX_BATCH_SIZE."""
    if len(items) > MAX_BATCH_SIZE:
        raise ValueError(f"Batch too large: {len(items)} {kind} (max {MAX_BATCH_SIZE})")
