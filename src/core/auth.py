"""Authentication module for Contex"""

import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select

from src.core.database import DatabaseManager
from src.core.db_models import APIKey as APIKeyModel
from src.core.keyhash import hash_api_key
from src.core.logging import get_logger
from src.core.rbac import Role, assign_role

logger = get_logger(__name__)


async def create_api_key(
    db: DatabaseManager,
    name: str,
    tenant_id: Optional[str] = None,
    raw_key: Optional[str] = None,
) -> tuple[str, str]:
    """Create an API key, generating the raw key unless one is given; return (raw_key, key_id)."""
    raw_key = raw_key or f"ck_{secrets.token_urlsafe(32)}"
    key_hash = hash_api_key(raw_key)
    key_id = secrets.token_hex(8)
    created_at = datetime.now(timezone.utc)

    async with db.session() as session:
        session.add(APIKeyModel(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            prefix=raw_key[:7],
            tenant_id=tenant_id,
            created_at=created_at,
        ))

    logger.info("API key created", key_id=key_id, name=name)

    return raw_key, key_id


async def bootstrap_admin(db: DatabaseManager) -> None:
    """Create an admin key when none exist, from BOOTSTRAP_ADMIN_KEY or generated."""
    async with db.session() as session:
        if (await session.execute(select(APIKeyModel.key_id).limit(1))).first():
            return
    provided = os.getenv("BOOTSTRAP_ADMIN_KEY")
    name = os.getenv("BOOTSTRAP_ADMIN_NAME", "root")
    raw_key, key_id = await create_api_key(db, name, raw_key=provided)
    await assign_role(db, key_id, Role.ADMIN)
    logger.warning("Bootstrap admin key created", key_id=key_id, name=name)
    if not provided:
        # stderr only: logger records also go to Sentry and log forwarders.
        print(
            "\n" + "=" * 60
            + "\nBOOTSTRAP ADMIN KEY (shown once; mint more with python -m src.cli keys create):"
            + f"\n   API Key: {raw_key}"
            + f"\n   Key ID: {key_id}"
            + "\n" + "=" * 60 + "\n",
            file=sys.stderr,
        )


async def revoke_api_key(db: DatabaseManager, key_id: str) -> bool:
    """Revoke an API key by ID; False if it does not exist."""
    async with db.session() as session:
        result = await session.execute(delete(APIKeyModel).where(APIKeyModel.key_id == key_id))
    if result.rowcount > 0:
        logger.info("API key revoked", key_id=key_id)
        return True
    return False

