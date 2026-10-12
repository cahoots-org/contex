"""Authentication module for Contex"""

import os
import secrets
import sys
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy import delete, select

from src.core.database import DatabaseManager
from src.core.db_models import APIKey as APIKeyModel
from src.core.keyhash import hash_api_key
from src.core.logging import get_logger
from src.core.rbac import Role, assign_role

logger = get_logger(__name__)


class APIKey(BaseModel):
    """API Key response model"""
    key_id: str
    name: str
    prefix: str
    scopes: List[str] = []
    created_at: str
    tenant_id: Optional[str] = None


async def create_api_key(
    db: DatabaseManager,
    name: str,
    scopes: List[str] = None,
    tenant_id: Optional[str] = None,
    raw_key: Optional[str] = None,
) -> tuple[str, APIKey]:
    """Create an API key, generating the raw key unless one is given.

    Returns (raw_key, APIKey model).
    """
    raw_key = raw_key or f"ck_{secrets.token_urlsafe(32)}"
    key_hash = hash_api_key(raw_key)
    key_id = secrets.token_hex(8)
    created_at = datetime.now(timezone.utc)

    async with db.session() as session:
        # Create database record
        api_key_record = APIKeyModel(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            prefix=raw_key[:7],
            scopes=scopes or [],
            tenant_id=tenant_id,
            created_at=created_at,
        )
        session.add(api_key_record)

    # Return response model
    api_key = APIKey(
        key_id=key_id,
        name=name,
        prefix=raw_key[:7],
        scopes=scopes or [],
        created_at=created_at.isoformat(),
        tenant_id=tenant_id,
    )

    logger.info("API key created", key_id=key_id, name=name)

    return raw_key, api_key


async def bootstrap_admin(db: DatabaseManager) -> None:
    """Create an admin key when none exist, from BOOTSTRAP_ADMIN_KEY or generated."""
    if await list_api_keys(db):
        return
    provided = os.getenv("BOOTSTRAP_ADMIN_KEY")
    raw_key, api_key = await create_api_key(
        db, os.getenv("BOOTSTRAP_ADMIN_NAME", "root"), raw_key=provided
    )
    await assign_role(db, api_key.key_id, Role.ADMIN)
    logger.warning("Bootstrap admin key created", key_id=api_key.key_id, name=api_key.name)
    if not provided:
        # stderr only: logger records also go to Sentry and log forwarders.
        print(
            "\n" + "=" * 60
            + "\nBOOTSTRAP ADMIN KEY (shown once; mint more with python -m src.cli keys create):"
            + f"\n   API Key: {raw_key}"
            + f"\n   Key ID: {api_key.key_id}"
            + "\n" + "=" * 60 + "\n",
            file=sys.stderr,
        )


async def revoke_api_key(
    db: DatabaseManager, key_id: str, tenant_id: Optional[str] = None
) -> bool:
    """
    Revoke an API key by ID.

    Args:
        db: Database manager
        key_id: Key ID to revoke
        tenant_id: When provided, only revokes the key if it belongs to this tenant

    Returns:
        True if key was revoked, False if not found or tenant mismatch
    """
    async with db.session() as session:
        if tenant_id is not None:
            result = await session.execute(
                delete(APIKeyModel).where(
                    APIKeyModel.key_id == key_id,
                    APIKeyModel.tenant_id == tenant_id,
                )
            )
            if result.rowcount > 0:
                logger.info("API key revoked", key_id=key_id)
                return True
            return False

        result = await session.execute(
            delete(APIKeyModel).where(APIKeyModel.key_id == key_id)
        )

        if result.rowcount > 0:
            logger.info("API key revoked", key_id=key_id)
            return True

    return False


async def list_api_keys(db: DatabaseManager, tenant_id: Optional[str] = None) -> List[APIKey]:
    """
    List all API keys.

    Args:
        db: Database manager
        tenant_id: Optional tenant ID to filter by

    Returns:
        List of API keys
    """
    async with db.session() as session:
        query = select(APIKeyModel)
        if tenant_id:
            query = query.where(APIKeyModel.tenant_id == tenant_id)

        result = await session.execute(query)
        keys = result.scalars().all()

        return [
            APIKey(
                key_id=k.key_id,
                name=k.name,
                prefix=k.prefix,
                scopes=k.scopes or [],
                created_at=k.created_at.isoformat() if k.created_at else "",
            )
            for k in keys
        ]
