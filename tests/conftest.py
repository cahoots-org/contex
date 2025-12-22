"""Pytest configuration and fixtures for PostgreSQL-based tests"""

import os
import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from unittest.mock import AsyncMock, MagicMock
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy import text

# Set test database URL before any imports that might use it
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test"
)

from src.core.database import DatabaseManager


class TestDatabaseManager(DatabaseManager):
    """Test-specific database manager with transaction rollback."""

    def __init__(self):
        super().__init__()
        self._test_session = None

    async def connect_test(
        self,
        database_url: str = None,
    ) -> None:
        """Connect to test database."""
        url = database_url or os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test"
        )

        self.engine = create_async_engine(
            url,
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


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[DatabaseManager, None]:
    """
    Create a database manager for testing.

    Uses the test database and cleans up after each test.
    """
    manager = TestDatabaseManager()

    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test"
    )

    try:
        await manager.connect_test(database_url)

        # Create tables if they don't exist
        from src.core.db_models import Base
        async with manager.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        yield manager

    finally:
        # Clean up test data
        if manager.is_connected:
            try:
                async with manager.session() as session:
                    # Delete in reverse order of dependencies
                    await session.execute(text("DELETE FROM webhook_deliveries"))
                    await session.execute(text("DELETE FROM webhook_endpoints"))
                    await session.execute(text("DELETE FROM audit_events"))
                    await session.execute(text("DELETE FROM rate_limit_entries"))
                    await session.execute(text("DELETE FROM agent_registrations"))
                    await session.execute(text("DELETE FROM embeddings"))
                    await session.execute(text("DELETE FROM snapshots"))
                    await session.execute(text("DELETE FROM events"))
                    await session.execute(text("DELETE FROM service_account_keys"))
                    await session.execute(text("DELETE FROM service_accounts"))
                    await session.execute(text("DELETE FROM api_key_roles"))
                    await session.execute(text("DELETE FROM api_keys"))
                    await session.execute(text("DELETE FROM tenant_projects"))
                    await session.execute(text("DELETE FROM tenant_usage"))
                    await session.execute(text("DELETE FROM tenants"))
            except Exception:
                pass  # Tables might not exist yet

            await manager.disconnect()


@pytest_asyncio.fixture
async def redis():
    """
    Create a clean Redis client for each test.

    Uses FakeRedis for fast, isolated testing.
    Used for pub/sub testing only.
    """
    client = FakeAsyncRedis(decode_responses=False)
    yield client
    # Cleanup
    await client.flushall()
    await client.aclose()


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


@pytest.fixture
def mock_redis():
    """
    Create a mock Redis client for unit tests.
    """
    mock = AsyncMock()
    mock.publish = AsyncMock(return_value=1)
    mock.subscribe = AsyncMock()
    return mock


# Configure pytest-asyncio
def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )


# Skip integration tests if database is not available
def pytest_collection_modifyitems(config, items):
    """Skip integration tests if DATABASE_URL is not set."""
    if not os.getenv("DATABASE_URL"):
        skip_integration = pytest.mark.skip(reason="DATABASE_URL not set")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)
