"""Unit tests for the pure mapping functions in connectors.postgres.mapping."""
from __future__ import annotations

import pytest

from connectors.postgres.mapping import (
    build_data_key,
    is_binary,
    resolve_pk,
    row_to_event,
    select_columns,
)


class TestIsBinary:
    def test_bytea(self):
        assert is_binary("bytea") is True

    def test_bit(self):
        assert is_binary("bit") is True

    def test_varbit(self):
        assert is_binary("varbit") is True

    def test_bit_varying(self):
        assert is_binary("bit varying") is True

    def test_uppercase(self):
        assert is_binary("BYTEA") is True

    def test_text_is_not_binary(self):
        assert is_binary("text") is False

    def test_integer_is_not_binary(self):
        assert is_binary("integer") is False

    def test_jsonb_is_not_binary(self):
        assert is_binary("jsonb") is False


class TestResolvePk:
    def test_introspected_pk_wins(self):
        result = resolve_pk("public", "users", ["id"], {"public.users": "user_id"})
        assert result == ["id"]

    def test_falls_back_to_key_columns(self):
        result = resolve_pk("public", "events", [], {"public.events": "event_id"})
        assert result == ["event_id"]

    def test_list_override(self):
        result = resolve_pk("public", "events", [], {"public.events": ["a", "b"]})
        assert result == ["a", "b"]

    def test_no_pk_no_override_returns_none(self):
        result = resolve_pk("public", "views", [], {})
        assert result is None

    def test_composite_pk(self):
        result = resolve_pk("public", "order_items", ["order_id", "item_id"], {})
        assert result == ["order_id", "item_id"]


class TestBuildDataKey:
    def test_simple_pk(self):
        row = {"id": 42, "name": "Alice"}
        assert build_data_key("public", "users", row, ["id"]) == "public.users.42"

    def test_composite_pk(self):
        row = {"order_id": 1, "item_id": 7, "qty": 3}
        key = build_data_key("public", "order_items", row, ["order_id", "item_id"])
        assert key == "public.order_items.1:7"

    def test_missing_key_col_renders_empty(self):
        row = {"name": "Alice"}
        key = build_data_key("public", "users", row, ["id"])
        assert key == "public.users."

    def test_none_value_renders_as_none_string(self):
        row = {"id": None}
        assert build_data_key("public", "t", row, ["id"]) == "public.t.None"


class TestSelectColumns:
    def _cols(self, *pairs):
        return [{"name": n, "type": t} for n, t in pairs]

    def test_binary_skipped_by_default(self):
        cols = self._cols(("id", "integer"), ("data", "bytea"), ("name", "text"))
        result = select_columns(cols, include_binary=False, col_include=None, col_exclude=None)
        assert result == ["id", "name"]

    def test_binary_included_when_opted_in(self):
        cols = self._cols(("id", "integer"), ("data", "bytea"))
        result = select_columns(cols, include_binary=True, col_include=None, col_exclude=None)
        assert result == ["id", "data"]

    def test_exclude_glob(self):
        cols = self._cols(("id", "integer"), ("password", "text"), ("email", "text"))
        result = select_columns(cols, include_binary=False, col_include=None, col_exclude=["password"])
        assert result == ["id", "email"]

    def test_exclude_wildcard(self):
        cols = self._cols(("id", "integer"), ("api_token", "text"), ("refresh_token", "text"))
        result = select_columns(
            cols, include_binary=False, col_include=None, col_exclude=["*_token"]
        )
        assert result == ["id"]

    def test_include_glob(self):
        cols = self._cols(("id", "integer"), ("name", "text"), ("internal_notes", "text"))
        result = select_columns(
            cols, include_binary=False, col_include=["id", "name"], col_exclude=None
        )
        assert result == ["id", "name"]

    def test_include_and_exclude_combined(self):
        cols = self._cols(("id", "integer"), ("name", "text"), ("secret_key", "text"))
        result = select_columns(
            cols, include_binary=False, col_include=["id", "name", "secret_key"], col_exclude=["secret_*"]
        )
        assert result == ["id", "name"]

    def test_varbit_excluded(self):
        cols = self._cols(("id", "integer"), ("flags", "varbit"))
        result = select_columns(cols, include_binary=False, col_include=None, col_exclude=None)
        assert result == ["id"]

    def test_empty_column_list(self):
        result = select_columns([], include_binary=False, col_include=None, col_exclude=None)
        assert result == []


class TestRowToEvent:
    def test_basic(self):
        row = {"id": 1, "name": "Alice", "score": 9.5}
        event = row_to_event("public", "users", row, ["id"], ["id", "name", "score"])
        assert event.op == "upsert"
        assert event.key == "public.users.1"
        assert event.payload == {"id": 1, "name": "Alice", "score": 9.5}
        assert event.data_format == "json"
        assert event.source_meta == {"source": "postgres", "schema": "public", "table": "users"}

    def test_selected_columns_filter_payload(self):
        row = {"id": 1, "name": "Alice", "password": "secret"}
        event = row_to_event("public", "users", row, ["id"], ["id", "name"])
        assert "password" not in event.payload

    def test_composite_pk(self):
        row = {"order_id": 10, "item_id": 3, "qty": 2}
        event = row_to_event("public", "order_items", row, ["order_id", "item_id"], ["order_id", "item_id", "qty"])
        assert event.key == "public.order_items.10:3"

    def test_none_payload_value(self):
        row = {"id": 5, "description": None}
        event = row_to_event("public", "items", row, ["id"], ["id", "description"])
        assert event.payload["description"] is None

    def test_non_json_value_serialized_as_string(self):
        import decimal
        row = {"id": 1, "price": decimal.Decimal("9.99")}
        event = row_to_event("public", "products", row, ["id"], ["id", "price"])
        assert event.payload["price"] == "9.99"

    def test_boolean_preserved(self):
        row = {"id": 1, "active": True}
        event = row_to_event("public", "users", row, ["id"], ["id", "active"])
        assert event.payload["active"] is True
