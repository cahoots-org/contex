"""Unit tests for mapping Kafka messages to ChangeEvents."""
from __future__ import annotations

from types import SimpleNamespace

from connectors.kafka.mapping import message_to_event


def _msg(key=b"order-1", value=b'{"total": 3}', topic="orders", partition=0, offset=7,
         timestamp=1760000000000):
    return SimpleNamespace(topic=topic, partition=partition, offset=offset,
                           timestamp=timestamp, key=key, value=value)


def test_keyed_json_message_is_an_upsert():
    event = message_to_event("main", _msg(), "publish")
    assert event.op == "upsert"
    assert event.key == "main:orders/order-1"
    assert event.payload == {"total": 3}
    assert event.data_format == "json"
    assert event.published_at == "2025-10-09T08:53:20+00:00"


def test_tombstone_is_a_delete():
    event = message_to_event("main", _msg(value=None), "publish")
    assert event.op == "delete"
    assert event.key == "main:orders/order-1"


def test_non_json_value_is_text():
    event = message_to_event("main", _msg(value=b"plain words"), "publish")
    assert event.payload == "plain words"
    assert event.data_format == "text"


def test_json_scalar_is_text():
    event = message_to_event("main", _msg(value=b"42"), "publish")
    assert event.payload == "42"
    assert event.data_format == "text"


def test_unkeyed_message_is_keyed_by_position():
    event = message_to_event("main", _msg(key=None, partition=2, offset=99), "publish")
    assert event.op == "upsert"
    assert event.key == "main:orders/2/99"


def test_unkeyed_skip_drops_unkeyed_messages():
    assert message_to_event("main", _msg(key=None), "skip") is None


def test_unkeyed_tombstone_is_dropped():
    assert message_to_event("main", _msg(key=None, value=None), "publish") is None


def test_non_utf8_value_is_dropped():
    assert message_to_event("main", _msg(value=b"\xff\xfe"), "publish") is None


def test_non_utf8_key_is_hex_encoded():
    event = message_to_event("main", _msg(key=b"\xff\x01"), "publish")
    assert event.key == "main:orders/ff01"


def test_missing_timestamp_leaves_published_at_unset():
    assert message_to_event("main", _msg(timestamp=-1), "publish").published_at is None


def test_key_with_control_characters_is_hex_encoded():
    event = message_to_event("main", _msg(key=b"\x00\x00\x00\x07"), "publish")
    assert event.key == "main:orders/00000007"
