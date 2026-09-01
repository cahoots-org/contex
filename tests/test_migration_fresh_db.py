"""Fresh-database migration guardrails.

These tests reproduce the three bugs that broke a clean Contex install:

- #102: ``alembic upgrade head`` failed on a fresh DB because migration 001
  created ``embeddings.embedding`` as bytea then tried an invalid
  ``USING embedding::vector(384)`` cast (fails under asyncpg).
- #103: the HNSW vector index existed only in the migration, so the create_all
  path (and every test DB) silently fell back to a sequential scan.
- #108: create_all wrote no ``alembic_version`` row, so a bootstrapped DB could
  never take incremental migrations.

The tests provision a throwaway database on the same server as ``DATABASE_URL``,
so they exercise a genuinely empty Postgres rather than the shared test DB.
"""

import asyncio
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.schema_bootstrap import (
    _ALEMBIC_DIR,
    _ALEMBIC_INI,
    _alembic_head,
    create_all_and_stamp,
)

pytestmark = pytest.mark.asyncio


def _base_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test",
    )


def _swap_db(url: str, db_name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{db_name}"))


@pytest_asyncio.fixture
async def fresh_db_url():
    """Create a throwaway empty database and drop it afterwards."""
    base = _base_url()
    admin_url = _swap_db(base, "postgres")
    db_name = f"contex_fresh_{uuid.uuid4().hex[:12]}"

    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await admin_engine.dispose()

    try:
        yield _swap_db(base, db_name)
    finally:
        admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin_engine.connect() as conn:
                # Terminate any lingering connections so DROP succeeds.
                await conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :d AND pid <> pg_backend_pid()"
                    ),
                    {"d": db_name},
                )
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        finally:
            await admin_engine.dispose()


async def _upgrade_head(db_url: str) -> None:
    """Run `alembic upgrade head` in a worker thread.

    alembic's env.py drives an async engine via ``asyncio.run()``, which cannot be
    called from inside pytest-asyncio's running loop, so we offload it to a thread.
    """
    def _run():
        # env.py reads DATABASE_URL from the environment and overrides the config
        # url, so set it here for the duration of the upgrade rather than relying
        # on cfg's sqlalchemy.url. Restore the previous value afterwards.
        prev = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = db_url
        try:
            command.upgrade(_alembic_config(db_url), "head")
        finally:
            if prev is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev

    await asyncio.to_thread(_run)


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


async def _fetch_scalar(engine, sql, **params):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def test_alembic_upgrade_head_on_fresh_db(fresh_db_url):
    """`alembic upgrade head` must reach head on an empty Postgres (#102)."""
    # alembic's command API is sync; env.py drives an async engine internally.
    await _upgrade_head(fresh_db_url)

    engine = create_async_engine(fresh_db_url)
    try:
        # alembic_version stamped to head
        version = await _fetch_scalar(engine, "SELECT version_num FROM alembic_version")
        assert version == _alembic_head()

        # embedding column is a real vector(384), not bytea (#102)
        coltype = await _fetch_scalar(
            engine,
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'embeddings'::regclass AND attname = 'embedding'",
        )
        assert coltype == "vector(384)"

        # HNSW vector index exists (#103)
        indexdef = await _fetch_scalar(
            engine,
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'embeddings' AND indexname = 'idx_embeddings_vector'",
        )
        assert indexdef is not None
        assert "USING hnsw" in indexdef
        assert "vector_cosine_ops" in indexdef
    finally:
        await engine.dispose()


async def test_create_all_matches_alembic_and_stamps(fresh_db_url):
    """create_all path stamps alembic_version and creates the HNSW index (#103, #108)."""
    engine = create_async_engine(fresh_db_url)
    try:
        await create_all_and_stamp(engine)

        version = await _fetch_scalar(engine, "SELECT version_num FROM alembic_version")
        assert version == _alembic_head()

        coltype = await _fetch_scalar(
            engine,
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'embeddings'::regclass AND attname = 'embedding'",
        )
        assert coltype == "vector(384)"

        indexdef = await _fetch_scalar(
            engine,
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'embeddings' AND indexname = 'idx_embeddings_vector'",
        )
        assert indexdef is not None
        assert "USING hnsw" in indexdef

        # Because create_all stamped head, a subsequent `alembic upgrade head`
        # is a no-op rather than an error — future incremental migrations apply.
        await _upgrade_head(fresh_db_url)
        version_after = await _fetch_scalar(
            engine, "SELECT version_num FROM alembic_version"
        )
        assert version_after == _alembic_head()
    finally:
        await engine.dispose()
