"""Fresh-database migration guardrail.

Reproduces the bug that broke a clean Contex install (#102): ``alembic upgrade
head`` failed on a fresh DB because migration 001 created
``embeddings.embedding`` as bytea then tried an invalid
``USING embedding::vector(384)`` cast (fails under asyncpg). Also guards #103 by
asserting the HNSW vector index is present after migrating.

The schema now comes exclusively from alembic (both app boot and the test
fixtures run ``alembic upgrade head``), so this test provisions a genuinely empty
throwaway database and asserts the full migration chain reaches head with a real
``vector(384)`` column and the HNSW index.
"""

import asyncio
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.database import run_migrations_to_head

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


async def _fetch_scalar(engine, sql, **params):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def test_alembic_upgrade_head_on_fresh_db(fresh_db_url):
    """`alembic upgrade head` must reach head on an empty Postgres (#102, #103).

    run_migrations_to_head is synchronous and drives alembic's own event loop, so
    it is offloaded to a thread to avoid nesting inside the running test loop.
    """
    await asyncio.to_thread(run_migrations_to_head, fresh_db_url)

    engine = create_async_engine(fresh_db_url)
    try:
        # alembic recorded a version row (schema is migration-tracked)
        version = await _fetch_scalar(
            engine, "SELECT version_num FROM alembic_version"
        )
        assert version is not None

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
