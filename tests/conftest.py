"""Pytest configuration and fixtures for PostgreSQL-based tests"""

import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy import text

# Tests truncate every table, so they only run against TEST_DATABASE_URL. App code
# under test reads DATABASE_URL; point it at the same database.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from src.core.database import DatabaseManager, run_migrations_to_head
from src.core.embedder import OnnxEmbedder
from src.core.notifier import Notifier, listen_connect_kwargs
from src.core.semantic_matcher import _MODEL_CACHE
from src.core.tenant import ensure_default_tenant


class TestDatabaseManager(DatabaseManager):
    """Test-specific database manager with transaction rollback."""

    def __init__(self):
        super().__init__()
        self._test_session = None

    async def connect_test(self, database_url: str) -> None:
        """Connect to test database."""
        self.engine = create_async_engine(
            database_url,
            echo=False,
            poolclass=NullPool,
        )

        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        self._is_connected = True


@pytest.fixture(autouse=True)
def _evict_mock_models():
    """Drop mocked embedders from the process-wide model cache after each test.

    Fixtures that patch OnnxEmbedder still go through _load_model, which caches
    the mock under the real model name; real-embedding tests then rank random
    vectors.
    """
    yield
    for name, model in list(_MODEL_CACHE.items()):
        if not isinstance(model, OnnxEmbedder):
            del _MODEL_CACHE[name]


@pytest.fixture(scope="session")
def _migrated_schema():
    """Bring the test database schema up to alembic head, once per session.

    Contex has a single canonical schema path (`alembic upgrade head`); the test
    suite uses the same path the app uses at boot rather than
    `Base.metadata.create_all`. Running migrations once per session (instead of
    per test) keeps suite runtime sane. Individual tests get a clean slate via the
    per-test data cleanup in the `db` fixture, not by rebuilding the schema.

    run_migrations_to_head is synchronous (it drives alembic's own event loop),
    so this fixture is a plain sync, session-scoped fixture.
    """
    run_migrations_to_head(TEST_DATABASE_URL)


@pytest_asyncio.fixture
async def db(_migrated_schema) -> AsyncGenerator[DatabaseManager, None]:
    """
    Create a database manager for testing.

    Uses the test database and cleans up after each test.
    """
    manager = TestDatabaseManager()

    try:
        await manager.connect_test(TEST_DATABASE_URL)

        # Start every test from a clean slate. The schema is provisioned once per
        # session (`_migrated_schema`); truncating here makes each test immune to
        # rows left by a prior test — including a prior test whose teardown failed.
        await _reset_database(manager)

        yield manager

    finally:
        if manager.is_connected:
            await manager.disconnect()


@pytest_asyncio.fixture
async def notifier(db):
    """A started Notifier on the test database; fast keepalive so drops are seen quickly."""
    n = Notifier(listen_connect_kwargs(db.engine.url), keepalive=0.1)
    await n.start()
    yield n
    await n.stop()


async def _reset_database(manager: DatabaseManager) -> None:
    """Truncate every app table and re-seed the default tenant.

    One ``TRUNCATE ... CASCADE`` is order-independent and resets serial PKs, so it
    replaces a hand-maintained, FK-ordered ``DELETE`` list that silently missed
    new tables and swallowed errors. ``alembic_version`` is preserved so the schema
    stays at head; the default tenant (migration 007's invariant, required by
    ``NOT NULL`` tenant FKs) is re-created after the wipe.
    """
    async with manager.session() as session:
        rows = await session.execute(text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
        ))
        tables = [row[0] for row in rows]
        if tables:
            quoted = ", ".join(f'"{t}"' for t in tables)
            await session.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
            await session.commit()
    await ensure_default_tenant(manager)


@pytest.fixture
def mock_db():
    """
    Create a mock database manager for unit tests that don't need real DB.
    """
    mock = MagicMock(spec=DatabaseManager)
    mock.is_connected = True

    # Create an async context manager for session
    session_mock = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session_mock
    session_context.__aexit__.return_value = None
    mock.session.return_value = session_context

    mock.health_check = AsyncMock(return_value={"status": "healthy"})

    return mock


# Configure pytest-asyncio
def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )


def pytest_collection_modifyitems(config, items):
    """Skip database tests when TEST_DATABASE_URL is not set."""
    if TEST_DATABASE_URL:
        return
    skip_db = pytest.mark.skip(reason="TEST_DATABASE_URL not set")
    for item in items:
        if "integration" in item.keywords or "db" in getattr(item, "fixturenames", ()):
            item.add_marker(skip_db)
