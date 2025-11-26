"""Service Accounts for Machine-to-Machine Authentication

Service accounts provide a secure way for automated systems, CI/CD pipelines,
and other services to authenticate with Contex without user interaction.

Features:
- JWT-based authentication with short-lived tokens
- Key rotation support
- Scoped permissions
- Audit logging
"""

import os
import secrets
import hashlib
import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, UTC
from enum import Enum
from pydantic import BaseModel, Field
from redis.asyncio import Redis
import jwt

from src.core.logging import get_logger
from src.core.rbac import Role

logger = get_logger(__name__)


# JWT secret (should be from environment in production)
JWT_SECRET = os.getenv("SERVICE_ACCOUNT_JWT_SECRET", secrets.token_urlsafe(32))
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "contex"


class ServiceAccountType(str, Enum):
    """Types of service accounts"""
    AGENT = "agent"           # For AI agents
    CI_CD = "ci_cd"          # For CI/CD pipelines
    INTEGRATION = "integration"  # For external integrations
    INTERNAL = "internal"     # For internal services


class ServiceAccountKey(BaseModel):
    """Service account key for authentication"""
    key_id: str
    created_at: str
    expires_at: Optional[str] = None
    last_used: Optional[str] = None
    description: Optional[str] = None


class ServiceAccount(BaseModel):
    """Service account model"""
    account_id: str
    name: str
    description: Optional[str] = None
    account_type: ServiceAccountType
    tenant_id: Optional[str] = None

    # Permissions
    role: Role = Role.READONLY
    allowed_projects: List[str] = Field(default_factory=list)  # Empty = all projects
    scopes: List[str] = Field(default_factory=list)

    # Keys
    keys: List[ServiceAccountKey] = Field(default_factory=list)

    # Metadata
    created_at: str
    updated_at: Optional[str] = None
    created_by: Optional[str] = None
    is_active: bool = True

    # Usage tracking
    last_active: Optional[str] = None
    total_requests: int = 0


class ServiceAccountToken(BaseModel):
    """JWT token for service account authentication"""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int  # seconds
    expires_at: str


class ServiceAccountManager:
    """
    Manages service accounts for machine-to-machine authentication.

    Features:
    - Create/update/delete service accounts
    - Generate and rotate authentication keys
    - Issue JWT tokens
    - Validate tokens
    """

    KEY_PREFIX = "contex:service_account:"

    def __init__(self, redis: Redis):
        """
        Initialize service account manager.

        Args:
            redis: Redis connection
        """
        self.redis = redis

    async def create_account(
        self,
        name: str,
        account_type: ServiceAccountType,
        description: Optional[str] = None,
        tenant_id: Optional[str] = None,
        role: Role = Role.READONLY,
        allowed_projects: Optional[List[str]] = None,
        scopes: Optional[List[str]] = None,
        created_by: Optional[str] = None,
    ) -> tuple[ServiceAccount, str]:
        """
        Create a new service account.

        Args:
            name: Display name
            account_type: Type of service account
            description: Optional description
            tenant_id: Associated tenant (for multi-tenancy)
            role: RBAC role
            allowed_projects: List of allowed project IDs
            scopes: Additional permission scopes
            created_by: User/key that created this account

        Returns:
            Tuple of (ServiceAccount, initial_key)
        """
        account_id = f"sa_{secrets.token_hex(8)}"

        # Generate initial key
        raw_key = f"sak_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_id = secrets.token_hex(8)

        initial_key = ServiceAccountKey(
            key_id=key_id,
            created_at=datetime.now(UTC).isoformat(),
            description="Initial key",
        )

        account = ServiceAccount(
            account_id=account_id,
            name=name,
            description=description,
            account_type=account_type,
            tenant_id=tenant_id,
            role=role,
            allowed_projects=allowed_projects or [],
            scopes=scopes or [],
            keys=[initial_key],
            created_at=datetime.now(UTC).isoformat(),
            created_by=created_by,
        )

        # Store account
        await self._save_account(account)

        # Store key mapping
        await self.redis.hset(
            f"{self.KEY_PREFIX}key:{key_hash}",
            mapping={
                "account_id": account_id,
                "key_id": key_id,
            }
        )

        logger.info("Service account created",
                   account_id=account_id,
                   name=name,
                   account_type=account_type.value,
                   tenant_id=tenant_id)

        return account, raw_key

    async def get_account(self, account_id: str) -> Optional[ServiceAccount]:
        """Get service account by ID"""
        data = await self.redis.get(f"{self.KEY_PREFIX}account:{account_id}")
        if not data:
            return None
        return ServiceAccount.model_validate_json(data)

    async def update_account(
        self,
        account_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        role: Optional[Role] = None,
        allowed_projects: Optional[List[str]] = None,
        scopes: Optional[List[str]] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[ServiceAccount]:
        """Update service account"""
        account = await self.get_account(account_id)
        if not account:
            return None

        if name is not None:
            account.name = name
        if description is not None:
            account.description = description
        if role is not None:
            account.role = role
        if allowed_projects is not None:
            account.allowed_projects = allowed_projects
        if scopes is not None:
            account.scopes = scopes
        if is_active is not None:
            account.is_active = is_active

        account.updated_at = datetime.now(UTC).isoformat()

        await self._save_account(account)

        logger.info("Service account updated",
                   account_id=account_id,
                   is_active=account.is_active)

        return account

    async def delete_account(self, account_id: str) -> bool:
        """Delete service account and all its keys"""
        account = await self.get_account(account_id)
        if not account:
            return False

        # Delete all key mappings
        for key in account.keys:
            # We need to find the hash for each key
            async for redis_key in self.redis.scan_iter(f"{self.KEY_PREFIX}key:*"):
                data = await self.redis.hgetall(redis_key)
                if data.get(b"account_id", b"").decode() == account_id:
                    await self.redis.delete(redis_key)

        # Delete account
        await self.redis.delete(f"{self.KEY_PREFIX}account:{account_id}")

        # Remove from tenant index
        if account.tenant_id:
            await self.redis.srem(
                f"{self.KEY_PREFIX}tenant:{account.tenant_id}",
                account_id
            )

        logger.warning("Service account deleted", account_id=account_id)

        return True

    async def list_accounts(
        self,
        tenant_id: Optional[str] = None,
        account_type: Optional[ServiceAccountType] = None,
        is_active: Optional[bool] = None,
        limit: int = 100,
    ) -> List[ServiceAccount]:
        """List service accounts with optional filtering"""
        accounts = []

        if tenant_id:
            # Get from tenant index
            account_ids = await self.redis.smembers(
                f"{self.KEY_PREFIX}tenant:{tenant_id}"
            )
            for aid in account_ids:
                aid_str = aid.decode() if isinstance(aid, bytes) else aid
                account = await self.get_account(aid_str)
                if account:
                    accounts.append(account)
        else:
            # Scan all accounts
            async for key in self.redis.scan_iter(f"{self.KEY_PREFIX}account:*"):
                data = await self.redis.get(key)
                if data:
                    account = ServiceAccount.model_validate_json(data)
                    accounts.append(account)

        # Apply filters
        if account_type:
            accounts = [a for a in accounts if a.account_type == account_type]
        if is_active is not None:
            accounts = [a for a in accounts if a.is_active == is_active]

        return accounts[:limit]

    async def create_key(
        self,
        account_id: str,
        description: Optional[str] = None,
        expires_in_days: Optional[int] = None,
    ) -> Optional[tuple[str, ServiceAccountKey]]:
        """
        Create a new key for a service account.

        Args:
            account_id: Service account ID
            description: Key description
            expires_in_days: Key expiration in days

        Returns:
            Tuple of (raw_key, key_info) or None if account not found
        """
        account = await self.get_account(account_id)
        if not account:
            return None

        # Generate key
        raw_key = f"sak_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_id = secrets.token_hex(8)

        expires_at = None
        if expires_in_days:
            expires_at = (datetime.now(UTC) + timedelta(days=expires_in_days)).isoformat()

        key_info = ServiceAccountKey(
            key_id=key_id,
            created_at=datetime.now(UTC).isoformat(),
            expires_at=expires_at,
            description=description,
        )

        # Add to account
        account.keys.append(key_info)
        account.updated_at = datetime.now(UTC).isoformat()
        await self._save_account(account)

        # Store key mapping
        await self.redis.hset(
            f"{self.KEY_PREFIX}key:{key_hash}",
            mapping={
                "account_id": account_id,
                "key_id": key_id,
            }
        )

        logger.info("Service account key created",
                   account_id=account_id,
                   key_id=key_id)

        return raw_key, key_info

    async def revoke_key(self, account_id: str, key_id: str) -> bool:
        """Revoke a service account key"""
        account = await self.get_account(account_id)
        if not account:
            return False

        # Find and remove key
        key_found = False
        for key in account.keys:
            if key.key_id == key_id:
                account.keys.remove(key)
                key_found = True
                break

        if not key_found:
            return False

        account.updated_at = datetime.now(UTC).isoformat()
        await self._save_account(account)

        # Remove key mapping
        async for redis_key in self.redis.scan_iter(f"{self.KEY_PREFIX}key:*"):
            data = await self.redis.hgetall(redis_key)
            if data.get(b"key_id", b"").decode() == key_id:
                await self.redis.delete(redis_key)
                break

        logger.info("Service account key revoked",
                   account_id=account_id,
                   key_id=key_id)

        return True

    async def authenticate(self, raw_key: str) -> Optional[ServiceAccount]:
        """
        Authenticate with a service account key.

        Args:
            raw_key: Raw service account key

        Returns:
            ServiceAccount if valid, None otherwise
        """
        if not raw_key.startswith("sak_"):
            return None

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        # Look up key
        data = await self.redis.hgetall(f"{self.KEY_PREFIX}key:{key_hash}")
        if not data:
            return None

        account_id = data.get(b"account_id", b"").decode()
        key_id = data.get(b"key_id", b"").decode()

        # Get account
        account = await self.get_account(account_id)
        if not account or not account.is_active:
            return None

        # Find key and check expiration
        key_info = None
        for key in account.keys:
            if key.key_id == key_id:
                key_info = key
                break

        if not key_info:
            return None

        # Check expiration
        if key_info.expires_at:
            expires = datetime.fromisoformat(key_info.expires_at)
            if datetime.now(UTC) > expires:
                logger.warning("Service account key expired",
                             account_id=account_id,
                             key_id=key_id)
                return None

        # Update last used
        key_info.last_used = datetime.now(UTC).isoformat()
        account.last_active = datetime.now(UTC).isoformat()
        account.total_requests += 1
        await self._save_account(account)

        return account

    async def issue_token(
        self,
        account: ServiceAccount,
        expires_in: int = 3600,  # 1 hour default
    ) -> ServiceAccountToken:
        """
        Issue a JWT token for an authenticated service account.

        Args:
            account: Authenticated service account
            expires_in: Token lifetime in seconds

        Returns:
            ServiceAccountToken with JWT
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=expires_in)

        payload = {
            "sub": account.account_id,
            "name": account.name,
            "type": account.account_type.value,
            "role": account.role.value,
            "tenant_id": account.tenant_id,
            "projects": account.allowed_projects,
            "scopes": account.scopes,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": JWT_ISSUER,
        }

        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

        return ServiceAccountToken(
            access_token=token,
            expires_in=expires_in,
            expires_at=expires_at.isoformat(),
        )

    async def validate_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Validate a JWT token.

        Args:
            token: JWT token string

        Returns:
            Token claims if valid, None otherwise
        """
        try:
            payload = jwt.decode(
                token,
                JWT_SECRET,
                algorithms=[JWT_ALGORITHM],
                issuer=JWT_ISSUER,
            )
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Service account token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning("Invalid service account token", error=str(e))
            return None

    async def _save_account(self, account: ServiceAccount):
        """Save account to Redis"""
        await self.redis.set(
            f"{self.KEY_PREFIX}account:{account.account_id}",
            account.model_dump_json()
        )

        # Add to tenant index
        if account.tenant_id:
            await self.redis.sadd(
                f"{self.KEY_PREFIX}tenant:{account.tenant_id}",
                account.account_id
            )


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

_service_account_manager: Optional[ServiceAccountManager] = None


def init_service_account_manager(redis: Redis) -> ServiceAccountManager:
    """Initialize global service account manager"""
    global _service_account_manager
    _service_account_manager = ServiceAccountManager(redis)
    return _service_account_manager


def get_service_account_manager() -> Optional[ServiceAccountManager]:
    """Get global service account manager"""
    return _service_account_manager
