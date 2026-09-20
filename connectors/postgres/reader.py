"""Postgres reader: discover tables, introspect schema, page rows.

Connects read-only via ``source.dsn``, discovers base tables in accessible
schemas, applies table and column allow/deny lists, and yields a
:class:`~connectors.base.ChangeEvent` per row using a server-side cursor.
"""
from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

import psycopg
from psycopg.rows import dict_row

from connectors.base import ChangeEvent, allowed

from .mapping import resolve_pk, row_to_event, select_columns

logger = logging.getLogger(__name__)

_DISCOVER_SQL = """\
SELECT
    n.nspname  AS schema,
    c.relname  AS table
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND has_schema_privilege(n.nspname, 'USAGE')
  AND has_table_privilege(quote_ident(n.nspname) || '.' || quote_ident(c.relname), 'SELECT')
ORDER BY n.nspname, c.relname;
"""

_COLUMNS_SQL = """\
SELECT
    a.attname               AS name,
    pg_catalog.format_type(a.atttypid, a.atttypmod) AS type
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s
  AND c.relname = %s
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attnum;
"""

_PK_SQL = """\
SELECT
    a.attname AS column_name
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_attribute a
    ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
JOIN pg_catalog.pg_class c ON c.oid = i.indrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s
  AND c.relname = %s
  AND i.indisprimary
ORDER BY array_position(i.indkey, a.attnum);
"""


def _discover_tables(conn: psycopg.Connection, tbl_include, tbl_exclude) -> list[tuple[str, str]]:
    """Return (schema, table) pairs accessible to the current role."""
    with conn.cursor() as cur:
        cur.execute(_DISCOVER_SQL)
        rows = cur.fetchall()
    result = []
    for schema, table in rows:
        qualified = f"{schema}.{table}"
        if allowed(qualified, include=tbl_include, exclude=tbl_exclude):
            result.append((schema, table))
    return result


def _introspect_columns(conn: psycopg.Connection, schema: str, table: str) -> list[dict]:
    """Return column metadata as ``[{"name": ..., "type": ...}]``."""
    with conn.cursor() as cur:
        cur.execute(_COLUMNS_SQL, (schema, table))
        return [{"name": row[0], "type": row[1]} for row in cur.fetchall()]


def _introspect_pk(conn: psycopg.Connection, schema: str, table: str) -> list[str]:
    """Return the primary key column names in index order."""
    with conn.cursor() as cur:
        cur.execute(_PK_SQL, (schema, table))
        return [row[0] for row in cur.fetchall()]


def _page_rows(
    conn: psycopg.Connection,
    schema: str,
    table: str,
    columns: list[str],
    page_size: int = 1000,
) -> Generator[dict, None, None]:
    """Yield rows as dicts using a named server-side cursor."""
    quoted = (
        f"SELECT {', '.join(psycopg.sql.Identifier(c).as_string(conn) for c in columns)} "
        f"FROM {psycopg.sql.Identifier(schema, table).as_string(conn)}"
    )
    with conn.cursor(name="contex_reader", row_factory=dict_row) as cur:
        cur.execute(quoted)
        while True:
            batch = cur.fetchmany(page_size)
            if not batch:
                break
            yield from batch


def read_tables(
    dsn: str,
    tbl_include: list[str] | None,
    tbl_exclude: list[str] | None,
    col_include: list[str] | None,
    col_exclude: list[str] | None,
    key_columns: dict[str, Any],
    include_binary: bool,
    page_size: int = 1000,
) -> Generator[ChangeEvent, None, None]:
    """Connect to Postgres and yield a :class:`ChangeEvent` per selected row.

    Tables without a PK and no configured ``key_columns`` override are skipped
    with a warning.
    """
    # A named (server-side) cursor issues DECLARE CURSOR, which requires a
    # transaction — so we stay out of autocommit. read_only marks the whole
    # session read-only and gives every table a single consistent snapshot.
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        tables = _discover_tables(conn, tbl_include, tbl_exclude)
        logger.info("discovered %d table(s) after filtering", len(tables))

        for schema, table in tables:
            qualified = f"{schema}.{table}"
            all_cols = _introspect_columns(conn, schema, table)
            pk_cols = _introspect_pk(conn, schema, table)
            key_cols = resolve_pk(schema, table, pk_cols, key_columns)

            if key_cols is None:
                logger.warning(
                    "skipping %s — no primary key and no key_columns override", qualified
                )
                continue

            selected = select_columns(all_cols, include_binary, col_include, col_exclude)
            if not selected:
                logger.warning("skipping %s — no columns remain after filtering", qualified)
                continue

            # Key columns must always be fetched even if excluded from payload.
            fetch_cols = list(dict.fromkeys(key_cols + selected))
            row_count = 0
            for row in _page_rows(conn, schema, table, fetch_cols, page_size):
                yield row_to_event(schema, table, row, key_cols, selected)
                row_count += 1
            logger.info("%s: yielded %d row(s)", qualified, row_count)
