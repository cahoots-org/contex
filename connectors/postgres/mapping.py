"""Row-to-ChangeEvent mapping and column/table selection logic.

All functions here are pure (no I/O) so they can be unit-tested without a
live database.
"""
from __future__ import annotations

import logging
from typing import Any

from connectors.base import ChangeEvent, allowed

logger = logging.getLogger(__name__)

BINARY_TYPES = frozenset(
    {
        "bytea",
        "bit",
        "bit varying",
        "varbit",
    }
)


def is_binary(pg_type: str) -> bool:
    """True if ``pg_type`` is a Postgres binary type."""
    return pg_type.lower() in BINARY_TYPES


def resolve_pk(
    schema: str,
    table: str,
    pk_columns: list[str],
    key_columns: dict[str, str],
) -> list[str] | None:
    """Return the ordered list of key column names for a table.

    Prefers the introspected ``pk_columns``; falls back to a configured
    ``key_columns`` override; returns ``None`` when neither is available.
    """
    if pk_columns:
        return pk_columns
    qualified = f"{schema}.{table}"
    override = key_columns.get(qualified)
    if override:
        return [override] if isinstance(override, str) else list(override)
    return None


def build_data_key(schema: str, table: str, row: dict, key_cols: list[str]) -> str:
    """Build a stable ``data_key`` from the row's key column values.

    Composite keys are joined with ``:``. A missing key column value is
    rendered as the empty string so the key is always deterministic.
    """
    parts = [str(row.get(col, "")) for col in key_cols]
    pk_part = ":".join(parts)
    return f"{schema}.{table}.{pk_part}"


def select_columns(
    columns: list[dict],
    include_binary: bool,
    col_include: list[str] | None,
    col_exclude: list[str] | None,
) -> list[str]:
    """Return the ordered column names that pass include/exclude + binary filters.

    ``columns`` is a list of ``{"name": str, "type": str}`` dicts as returned
    by the schema introspection query. Binary columns are skipped unless
    ``include_binary`` is ``True``.
    """
    selected = []
    for col in columns:
        name: str = col["name"]
        pg_type: str = col["type"]
        if not include_binary and is_binary(pg_type):
            continue
        if not allowed(name, include=col_include, exclude=col_exclude):
            continue
        selected.append(name)
    return selected


def row_to_event(
    schema: str,
    table: str,
    row: dict,
    key_cols: list[str],
    selected_columns: list[str],
) -> ChangeEvent:
    """Map a single database row to a :class:`ChangeEvent`.

    ``selected_columns`` is the pre-computed list from :func:`select_columns`.
    ``row`` is a mapping of all columns; only ``selected_columns`` are placed
    in the payload.
    """
    payload = {col: _serialize(row[col]) for col in selected_columns if col in row}
    key = build_data_key(schema, table, row, key_cols)
    return ChangeEvent(
        op="upsert",
        key=key,
        payload=payload,
        source_meta={"source": "postgres", "schema": schema, "table": table},
        data_format="json",
    )


def _serialize(value: Any) -> Any:
    """Coerce non-JSON-serialisable Postgres values to strings."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)
