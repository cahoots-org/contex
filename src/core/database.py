"""
PostgreSQL Database Connection Manager

Provides async SQLAlchemy engine and session management for Contex.
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.core.logging import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """Manages PostgreSQL database connections using SQLAlchemy async."""

    def __init__(self):
        self.engine: Optional[AsyncEngine] = None
        self.session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._is_connected = False

    async def connect(
        self,
        database_url: Optional[str] = None,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30.0,
        pool_recycle: int = 1800,
        echo: bool = False,
    ) -> None:
        """
        Connect to the PostgreSQL database.

        Args:
            database_url: PostgreSQL connection URL (postgresql+asyncpg://...)
            pool_size: Number of connections to keep in the pool
            max_overflow: Maximum overflow connections above pool_size
            pool_timeout: Timeout waiting for connection from pool
            pool_recycle: Recycle connections after this many seconds
            echo: Echo SQL statements to log
        """
        if self._is_connected:
            logger.warning("Database already connected")
            return

        url = database_url or os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://contex:contex_password@localhost:5432/contex"
        )

        # For testing with SQLite, use NullPool
        if "sqlite" in url:
            self.engine = create_async_engine(
                url,
                echo=echo,
                poolclass=NullPool,
            )
        else:
            self.engine = create_async_engine(
                url,
                echo=echo,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_recycle=pool_recycle,
                pool_pre_ping=True,  # Verify connections before use
            )

        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        # Test connection
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            self._is_connected = True
            logger.info("Database connection established", url=url.split("@")[-1])
        except Exception as e:
            logger.error("Failed to connect to database", error=str(e))
            raise

    async def disconnect(self) -> None:
        """Close database connections."""
        if self.engine:
            await self.engine.dispose()
            self._is_connected = False
            logger.info("Database connection closed")

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Get an async database session.

        Usage:
            async with db.session() as session:
                result = await session.execute(query)
                await session.commit()
        """
        if not self.session_factory:
            raise RuntimeError("Database not connected. Call connect() first.")

        session = self.session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @asynccontextmanager
    async def session_no_commit(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Get an async database session without auto-commit.
        Caller is responsible for committing.
        """
        if not self.session_factory:
            raise RuntimeError("Database not connected. Call connect() first.")

        session = self.session_factory()
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @property
    def is_connected(self) -> bool:
        """Check if database is connected."""
        return self._is_connected

    async def health_check(self) -> dict:
        """
        Perform a health check on the database connection.

        Returns:
            dict with status, latency, and pool info
        """
        import time

        if not self._is_connected or not self.engine:
            return {
                "status": "unhealthy",
                "error": "Not connected",
            }

        try:
            start = time.perf_counter()
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            latency_ms = (time.perf_counter() - start) * 1000

            pool = self.engine.pool
            pool_status = {
                "size": pool.size() if hasattr(pool, "size") else None,
                "checked_in": pool.checkedin() if hasattr(pool, "checkedin") else None,
                "checked_out": pool.checkedout() if hasattr(pool, "checkedout") else None,
                "overflow": pool.overflow() if hasattr(pool, "overflow") else None,
            }

            status = "healthy" if latency_ms < 100 else "degraded"

            return {
                "status": status,
                "latency_ms": round(latency_ms, 2),
                "pool": pool_status,
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
            }


# Global database manager instance
_db_manager: Optional[DatabaseManager] = None


async def get_database() -> DatabaseManager:
    """Get the global database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


async def init_database(database_url: Optional[str] = None, **kwargs) -> DatabaseManager:
    """Initialize and connect the global database manager."""
    global _db_manager
    _db_manager = DatabaseManager()
    await _db_manager.connect(database_url=database_url, **kwargs)
    return _db_manager


async def close_database() -> None:
    """Close the global database connection."""
    global _db_manager
    if _db_manager:
        await _db_manager.disconnect()
        _db_manager = None
