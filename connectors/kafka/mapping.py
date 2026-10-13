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


def _key_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.hex()


def _published_at(timestamp_ms) -> str | None:
    if timestamp_ms is None or timestamp_ms < 0:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


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
    except ValueError:
        payload = None
    published_at = _published_at(msg.timestamp)
    if isinstance(payload, dict):
        return ChangeEvent(op="upsert", key=key, payload=payload, published_at=published_at)
    return ChangeEvent(op="upsert", key=key, payload=text, data_format="text",
                       published_at=published_at)
