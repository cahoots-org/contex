# src/core/identity.py
"""Single source of truth for caller identity."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from src.core.database import DatabaseManager
from src.core.db_models import APIKey as APIKeyModel, APIKeyRole as APIKeyRoleModel
from src.core.keyhash import candidate_hashes
from src.core.logging import get_logger
from src.core.rbac import Permission, Role, expand_role

logger = get_logger(__name__)

_KEY_PREFIX = "ck_"


@dataclass(frozen=True)
class Identity:
    key_id: str
    scopes: frozenset[Permission]
    tenant_id: str | None
    projects: tuple[str, ...]           # empty = all projects within tenant


async def resolve_identity(db: DatabaseManager, raw_key: str) -> Identity | None:
    """Resolve a raw bearer credential to an Identity, or None (deny)."""
    if not raw_key or not raw_key.startswith(_KEY_PREFIX):
        logger.warning("Rejected API key", reason="malformed")
        return None
    async with db.session() as session:
        key = (await session.execute(
            select(APIKeyModel).where(APIKeyModel.key_hash.in_(candidate_hashes(raw_key)))
        )).scalar_one_or_none()
        if key is None:
            logger.warning("Rejected API key", reason="unknown")
            return None

        role_row = (await session.execute(
            select(APIKeyRoleModel).where(APIKeyRoleModel.key_id == key.key_id)
        )).scalar_one_or_none()

        role = Role(role_row.role) if role_row else None
        projects = tuple(role_row.projects or ()) if role_row else ()

        # A key with no role is valid but powerless.
        scopes = expand_role(role) if role is not None else frozenset()

        return Identity(
            key_id=key.key_id,
            scopes=scopes,
            tenant_id=key.tenant_id,
            projects=projects,
        )

