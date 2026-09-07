# src/core/identity.py
"""Single source of truth for caller identity. Scopes are authoritative."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select

from src.core.database import DatabaseManager
from src.core.db_models import APIKey as APIKeyModel, APIKeyRole as APIKeyRoleModel
from src.core.rbac import Permission, Role, expand_role

_KEY_PREFIX = "ck_"


@dataclass(frozen=True)
class Identity:
    key_id: str
    scopes: frozenset[Permission]
    tenant_id: str | None
    projects: tuple[str, ...]           # empty = all projects within tenant
    role: Role | None                   # preset label; not consulted for authz

    def has_project(self, project_id: str | None) -> bool:
        if not self.projects:
            return True
        if project_id is None:
            return True
        return project_id in self.projects


# Used ONLY when AUTH_ENABLED is false (demo mode). Never returned by resolve_identity.
ANONYMOUS_IDENTITY = Identity(
    key_id="anonymous",
    scopes=frozenset(Permission),
    tenant_id=None,
    projects=(),
    role=Role.ADMIN,
)


async def resolve_identity(db: DatabaseManager, raw_key: str) -> Identity | None:
    """Resolve a raw bearer credential to an Identity, or None (deny)."""
    if not raw_key or not raw_key.startswith(_KEY_PREFIX):
        return None
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    async with db.session() as session:
        key = (await session.execute(
            select(APIKeyModel).where(APIKeyModel.key_hash == key_hash)
        )).scalar_one_or_none()
        if key is None:
            return None

        role_row = (await session.execute(
            select(APIKeyRoleModel).where(APIKeyRoleModel.key_id == key.key_id)
        )).scalar_one_or_none()

        role = Role(role_row.role) if role_row else None
        projects = tuple(role_row.projects or ()) if role_row else ()

        # Scopes are authoritative: explicit key.scopes win; else expand the role.
        explicit = _parse_scopes(key.scopes)
        if explicit:
            scopes = explicit
        elif role is not None:
            scopes = expand_role(role)
        else:
            scopes = frozenset()  # a key with neither is valid but powerless

        return Identity(
            key_id=key.key_id,
            scopes=scopes,
            tenant_id=key.tenant_id,
            projects=projects,
            role=role,
        )


def _parse_scopes(raw: list[str] | None) -> frozenset[Permission]:
    """Parse stored scope strings into Permissions, ignoring unrecognized values."""
    out = set()
    for s in raw or []:
        try:
            out.add(Permission(s))
        except ValueError:
            continue
    return frozenset(out)
