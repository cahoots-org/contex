"""Schema bootstrap helpers shared by the app lifespan and the test harness.

Contex has two ways a schema can come into existence on a fresh database:

1. ``alembic upgrade head`` — the canonical, incremental migration path.
2. ``Base.metadata.create_all`` — a convenience path used by the app lifespan
   and the test fixtures to stand up the whole schema in one shot.

These two must stay reconcilable. The alembic migrations and the ORM models are
kept in agreement (same ``vector(384)`` column, same HNSW index, etc.), so the
schemas they produce are equivalent. The one thing ``create_all`` does NOT do is
record which migration revision the schema corresponds to, which leaves
``alembic_version`` empty and makes future ``alembic upgrade`` runs impossible.

``create_all_and_stamp`` closes that gap: after building the schema via
``create_all`` it stamps ``alembic_version`` to head, so a database bootstrapped
this way can still take incremental migrations later.
"""

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.db_models import Base

# Repo root = three parents up from this file (src/core/schema_bootstrap.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_ALEMBIC_DIR = _REPO_ROOT / "alembic"


def _alembic_head() -> str:
    """Return the head revision of the alembic script directory."""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("No alembic head revision found")
    return head


def _stamp_head_sync(connection: Connection) -> None:
    """Stamp alembic_version to head using a sync connection.

    Runs inside ``connection.run_sync`` from an async caller. Only stamps when no
    version row exists yet, so it is safe to call on databases that were built via
    ``alembic upgrade`` (where alembic already recorded the revision).
    """
    context = MigrationContext.configure(connection)
    if context.get_current_revision() is not None:
        return
    context.stamp(ScriptDirectory.from_config(_config_for_stamp()), _alembic_head())


def _config_for_stamp() -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    return cfg


async def create_all_and_stamp(engine: AsyncEngine, *, tables=None) -> None:
    """Create the schema via ``create_all`` and stamp ``alembic_version`` to head.

    - Ensures the pgvector extension exists (required by the embeddings table).
    - Creates all ORM tables (or the provided subset).
    - Stamps ``alembic_version`` to the alembic head so the resulting database is
      indistinguishable, for migration-tracking purposes, from one built with
      ``alembic upgrade head`` — future incremental migrations will apply.
    """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        if tables is not None:
            await conn.run_sync(Base.metadata.create_all, tables=tables)
        else:
            await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_stamp_head_sync)
