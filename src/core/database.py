"""
PostgreSQL Database Connection Manager

Provides async SQLAlchemy engine and session management for Contex.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

import anyio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.core.config import DEFAULT_DATABASE_URL
from src.core.json_utils import json_safe_default
from src.core.limits import positive_int_env
from src.core.logging import get_logger

logger = get_logger(__name__)


# Repo root = three parents up from this file (src/core/database.py). The alembic
# config and migration scripts live at the repo root and MUST be present in any
# runtime image (see Dockerfile COPY of alembic/ + alembic.ini), otherwise a
# migrate-at-boot deployment crash-loops.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_ALEMBIC_DIR = _REPO_ROOT / "alembic"


def _alembic_config(database_url: str) -> Config:
    """Build an alembic Config pointed at our script dir and the given asyncpg URL."""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


def run_migrations_to_head(database_url: str) -> None:
    """Synchronously run ``alembic upgrade head`` against ``database_url``."""
    command.upgrade(_alembic_config(database_url), "head")


async def _assert_migratable(url: str) -> None:
    """Refuse to migrate a populated database that alembic never stamped.

    A database with application tables but no ``alembic_version`` row was created
    outside the migration chain (e.g. an old ``create_all``). Running
    ``alembic upgrade head`` against it replays from revision 001 and crash-loops
    on ``DuplicateTableError``. Fail fast with an actionable message instead — this
    is exactly the state that once crash-looped production.

    Extension-owned tables do not count. The ParadeDB image ships ``postgis``,
    whose ``public.spatial_ref_sys`` exists in every freshly created database, so
    a plain ``count(*)`` over ``public`` sees it and refuses to migrate an
    otherwise-empty database — breaking first boot for every new install. Only
    tables not owned by an extension (``pg_depend.deptype = 'e'``) signal a
    create_all-origin schema.
    """
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            if await conn.scalar(text("SELECT to_regclass('public.alembic_version')")) is not None:
                return
            table_count = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relkind = 'r' AND n.nspname = 'public' "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM pg_depend d "
                    "  WHERE d.objid = c.oid AND d.deptype = 'e'"
                    ")"
                )
            )
            if table_count:
                raise RuntimeError(
                    "Database has tables but no alembic_version row: it was created "
                    "outside the migration chain (e.g. create_all). Refusing to run "
                    "'alembic upgrade head' — it would replay from revision 001 and "
                    "fail on the existing tables. Reset the database (migrate from "
                    "empty), or if the schema already matches head, stamp it with "
                    "'alembic stamp head'."
                )
    finally:
        await engine.dispose()


class DatabaseManager:
    """Manages PostgreSQL database connections using SQLAlchemy async."""

    def __init__(self):
        self.engine: Optional[AsyncEngine] = None
        self.session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._is_connected = False
        self._database_url: Optional[str] = None

    async def connect(
        self,
        database_url: Optional[str] = None,
        pool_size: Optional[int] = None,
        max_overflow: Optional[int] = None,
        pool_timeout: Optional[float] = None,
        pool_recycle: int = 1800,
        echo: bool = False,
    ) -> None:
        """
        Connect to the PostgreSQL database.

        Args:
            database_url: PostgreSQL connection URL (postgresql+asyncpg://...)
            pool_size: Number of connections to keep in the pool (env DB_POOL_SIZE, default 5)
            max_overflow: Maximum overflow connections above pool_size (env DB_MAX_OVERFLOW, default 10)
            pool_timeout: Seconds to wait for a pooled connection (env DB_POOL_TIMEOUT, default 30)
            pool_recycle: Recycle connections after this many seconds
            echo: Echo SQL statements to log
        """
        if self._is_connected:
            logger.warning("Database already connected")
            return

        url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        self._database_url = url
        if pool_size is None:
            pool_size = positive_int_env("DB_POOL_SIZE", 5)
        if max_overflow is None:
            max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
        if pool_timeout is None:
            pool_timeout = positive_int_env("DB_POOL_TIMEOUT", 30)

        self.engine = create_async_engine(
            url,
            echo=echo,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
            pool_pre_ping=True,  # Verify connections before use
            json_serializer=lambda o: json.dumps(o, default=json_safe_default),
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

    async def migrate_to_head(self, database_url: Optional[str] = None) -> None:
        """Bring the database schema up to the alembic head revision.

        This is the single, canonical schema path for Contex: both the app boot
        (main.py lifespan) and the test fixtures call this instead of
        ``Base.metadata.create_all``. Running the alembic chain guarantees the
        schema matches the migrations exactly (real ``vector(768)`` column, HNSW
        index, and an ``alembic_version`` row) and that future incremental
        migrations apply.

        alembic's command API is synchronous and its env.py drives its own engine
        via ``asyncio.run()``, which cannot run inside an already-running event
        loop. Since callers invoke this from async contexts, the blocking work is
        offloaded to a worker thread.
        """
        url = database_url or self._database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        await _assert_migratable(url)
        await asyncio.to_thread(run_migrations_to_head, url)

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
        except BaseException:
            # Shielded: a cancelled request must still return its connection to the pool.
            with anyio.CancelScope(shield=True):
                await session.rollback()
            raise
        finally:
            with anyio.CancelScope(shield=True):
                await session.close()

    @property
    def is_connected(self) -> bool:
        """Check if database is connected."""
        return self._is_connected


async def init_database(database_url: Optional[str] = None, **kwargs) -> DatabaseManager:
    """Create and connect a database manager."""
    db = DatabaseManager()
    await db.connect(database_url=database_url, **kwargs)
    return db
