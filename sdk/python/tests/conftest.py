"""
Pytest configuration and shared fixtures for Contex SDK tests.
"""

import pytest
import asyncio
from typing import Generator


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an event loop for the test session"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


def pytest_configure(config):
    """Configure custom pytest markers"""
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (require running server)"
    )
