"""Pytest configuration and fixtures"""

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis


@pytest_asyncio.fixture
async def redis():
    """
    Create a clean Redis client for each test.

    Uses FakeRedis for fast, isolated testing.
    Each test gets a fresh instance with no data.
    """
    client = FakeAsyncRedis(decode_responses=False)
    yield client
    # Cleanup
    await client.flushall()
    await client.aclose()
