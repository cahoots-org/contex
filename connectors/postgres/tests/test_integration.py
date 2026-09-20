"""Integration tests against a live Postgres instance.

These tests are skipped unless a Postgres connection is reachable.  In CI the
``POSTGRES_TEST_DSN`` environment variable is expected to point at a test DB
(e.g. paradedb/paradedb or vanilla postgres).  Locally the tests discover a
running container automatically if ``POSTGRES_TEST_DSN`` is unset.

The tests do NOT use a Contex server — they exercise the reader/mapping layer
directly by collecting yielded ChangeEvents, which is sufficient to validate
the full DB interaction path.
"""
from __future__ import annotations

import os

import pytest

_DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5432/postgres"


def _dsn() -> str:
    return os.environ.get("POSTGRES_TEST_DSN", _DEFAULT_DSN)


def _can_connect() -> bool:
    try:
        import psycopg

        conn = psycopg.connect(_dsn(), connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _can_connect(),
    reason="No Postgres reachable — set POSTGRES_TEST_DSN to run integration tests",
)


@requires_postgres
class TestReaderIntegration:
    @pytest.fixture(autouse=True)
    def setup_schema(self):
        """Create a throwaway schema with test tables for the duration of the test."""
        import psycopg

        dsn = _dsn()
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS contex_test CASCADE")
            conn.execute("CREATE SCHEMA contex_test")
            conn.execute(
                """
                CREATE TABLE contex_test.products (
                    id       SERIAL PRIMARY KEY,
                    name     TEXT NOT NULL,
                    price    NUMERIC(10,2),
                    active   BOOLEAN DEFAULT TRUE
                )
                """
            )
            conn.execute(
                """
                INSERT INTO contex_test.products (name, price, active)
                VALUES ('Widget', 9.99, TRUE), ('Gadget', 19.99, FALSE)
                """
            )
            conn.execute(
                """
                CREATE TABLE contex_test.order_items (
                    order_id INTEGER NOT NULL,
                    item_id  INTEGER NOT NULL,
                    qty      INTEGER,
                    PRIMARY KEY (order_id, item_id)
                )
                """
            )
            conn.execute(
                "INSERT INTO contex_test.order_items VALUES (1, 1, 3), (1, 2, 1)"
            )
            conn.execute(
                """
                CREATE TABLE contex_test.no_pk_table (
                    val TEXT
                )
                """
            )
            conn.execute("INSERT INTO contex_test.no_pk_table VALUES ('a')")
            conn.execute(
                """
                CREATE TABLE contex_test.binary_table (
                    id   SERIAL PRIMARY KEY,
                    blob BYTEA,
                    name TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO contex_test.binary_table (blob, name) VALUES ('\\xDEAD'::bytea, 'raw')"
            )
        yield
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS contex_test CASCADE")

    def _collect(self, **kwargs):
        from connectors.postgres.reader import read_tables

        defaults = dict(
            dsn=_dsn(),
            tbl_include=["contex_test.*"],
            tbl_exclude=None,
            col_include=None,
            col_exclude=None,
            key_columns={},
            include_binary=False,
        )
        defaults.update(kwargs)
        return list(defaults.pop("reader_fn", read_tables)(**defaults))

    def test_basic_rows_published(self):
        events = self._collect(tbl_include=["contex_test.products"])
        assert len(events) == 2
        keys = {e.key for e in events}
        assert "contex_test.products.1" in keys
        assert "contex_test.products.2" in keys

    def test_event_shape(self):
        events = self._collect(tbl_include=["contex_test.products"])
        e = next(e for e in events if e.key == "contex_test.products.1")
        assert e.op == "upsert"
        assert e.data_format == "json"
        assert e.source_meta == {"source": "postgres", "schema": "contex_test", "table": "products"}
        assert e.payload["name"] == "Widget"

    def test_composite_pk(self):
        events = self._collect(tbl_include=["contex_test.order_items"])
        keys = {e.key for e in events}
        assert "contex_test.order_items.1:1" in keys
        assert "contex_test.order_items.1:2" in keys

    def test_no_pk_table_skipped(self):
        events = self._collect(tbl_include=["contex_test.no_pk_table"])
        assert events == []

    def test_no_pk_with_key_columns_override(self):
        events = self._collect(
            tbl_include=["contex_test.no_pk_table"],
            key_columns={"contex_test.no_pk_table": "val"},
        )
        assert len(events) == 1
        assert events[0].key == "contex_test.no_pk_table.a"

    def test_binary_column_excluded_by_default(self):
        events = self._collect(tbl_include=["contex_test.binary_table"])
        assert len(events) == 1
        assert "blob" not in events[0].payload
        assert "name" in events[0].payload

    def test_binary_column_included_when_opted_in(self):
        events = self._collect(tbl_include=["contex_test.binary_table"], include_binary=True)
        assert "blob" in events[0].payload

    def test_column_exclude_glob(self):
        events = self._collect(
            tbl_include=["contex_test.products"], col_exclude=["price", "active"]
        )
        assert len(events) == 2
        for e in events:
            assert "price" not in e.payload
            assert "active" not in e.payload
            assert "name" in e.payload

    def test_table_exclude_glob(self):
        events = self._collect(
            tbl_include=["contex_test.*"],
            tbl_exclude=["contex_test.order_items", "contex_test.no_pk_table", "contex_test.binary_table"],
        )
        keys = {e.key for e in events}
        assert all(k.startswith("contex_test.products.") for k in keys)

    def test_upsert_idempotency(self):
        """Re-running produces the same keys (no duplication)."""
        from connectors.postgres.reader import read_tables

        kwargs = dict(
            dsn=_dsn(),
            tbl_include=["contex_test.products"],
            tbl_exclude=None,
            col_include=None,
            col_exclude=None,
            key_columns={},
            include_binary=False,
        )
        first = [e.key for e in read_tables(**kwargs)]
        second = [e.key for e in read_tables(**kwargs)]
        assert sorted(first) == sorted(second)
