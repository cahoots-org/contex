"""Guard: refuse to migrate a populated-but-unstamped database.

Reproduces the prod incident where a create_all-origin database (tables, no
alembic_version) crash-looped on `alembic upgrade head`. migrate_to_head must
now fail fast with a clear message instead.
"""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.core.database import DatabaseManager


def _url(dbname: str) -> str:
    base = os.environ["DATABASE_URL"].rsplit("/", 1)[0]
    return f"{base}/{dbname}"


@pytest.mark.asyncio
async def test_migrate_refuses_populated_unstamped_db():
    tmp = "contex_unstamped_guard"
    # CREATE/DROP DATABASE from the test DB itself (always present); a dedicated
    # maintenance DB like "contex" is not guaranteed in CI.
    admin = create_async_engine(
        os.environ["DATABASE_URL"], poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{tmp}"'))
            await conn.execute(text(f'CREATE DATABASE "{tmp}"'))

        # A table but no alembic_version — the dangerous state.
        seed = create_async_engine(_url(tmp), poolclass=NullPool)
        async with seed.begin() as conn:
            await conn.execute(text("CREATE TABLE tenants (tenant_id text PRIMARY KEY)"))
        await seed.dispose()

        with pytest.raises(RuntimeError, match="alembic_version"):
            await DatabaseManager().migrate_to_head(_url(tmp))
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{tmp}"'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_migrate_allows_empty_db(db):
    # An empty (no tables, no alembic_version) database migrates normally; a
    # properly-stamped one is a no-op. The session-migrated test DB already has
    # alembic_version, so this just confirms the guard doesn't false-positive.
    await db.migrate_to_head(os.environ["DATABASE_URL"])
