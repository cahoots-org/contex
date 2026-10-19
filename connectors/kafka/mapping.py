"""Map a Kafka message to a ChangeEvent, following log-compaction semantics.

The latest value per key is the record and a null value (tombstone) deletes
it. Unkeyed messages are an append-only log, keyed by their position.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from connectors.base import ChangeEvent

log = logging.getLogger(__name__)

# The MCP client can't serialise JSON nested past ~250 levels, and retrying
# never helps, so deeper values publish as text.
_MAX_JSON_DEPTH = 128


def _key_text(raw: bytes) -> str:
    """The key as text, or as hex when it isn't UTF-8 or holds control characters."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.hex()
    if any(ch < " " for ch in text):
        return raw.hex()
    return text


def _too_deep(value) -> bool:
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, (dict, list)):
            if depth > _MAX_JSON_DEPTH:
                return True
            children = node.values() if isinstance(node, dict) else node
            stack.extend((child, depth + 1) for child in children)
    return False


def _published_at(timestamp_ms) -> str | None:
    if timestamp_ms is None or timestamp_ms < 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def message_to_event(cluster: str, msg, unkeyed: str) -> ChangeEvent | None:
    """The event for one message, or None when there is nothing to publish."""
    if msg.key is None:
        if unkeyed == "skip" or msg.value is None:
            return None
        key = f"{cluster}:{msg.topic}/{msg.partition}/{msg.offset}"
    else:
        key = f"{cluster}:{msg.topic}/{_key_text(msg.key)}"
    if msg.value is None:
        return ChangeEvent(op="delete", key=key, payload=None)
    try:
        text = msg.value.decode("utf-8")
    except UnicodeDecodeError:
        log.warning("skip %s: value is not UTF-8", key)
        return None
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError):
        payload = None
    published_at = _published_at(msg.timestamp)
    if isinstance(payload, dict) and not _too_deep(payload):
        return ChangeEvent(op="upsert", key=key, payload=payload, published_at=published_at)
    return ChangeEvent(op="upsert", key=key, payload=text, data_format="text",
                       published_at=published_at)
