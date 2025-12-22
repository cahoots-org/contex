"""Service Accounts for Machine-to-Machine Authentication

Service accounts provide a secure way for automated systems, CI/CD pipelines,
and other services to authenticate with Contex without user interaction.

Features:
- JWT-based authentication with short-lived tokens
- Key rotation support
- Scoped permissions
- Audit logging
"""

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

import jwt
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update

from src.core.database import DatabaseManager
from src.core.db_models import ServiceAccount as ServiceAccountModel
from src.core.db_models import ServiceAccountKey as ServiceAccountKeyModel
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

    def __init__(self, db: DatabaseManager):
        """
        Initialize service account manager.

        Args:
            db: Database manager
        """
        self.db = db

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

        now = datetime.now(UTC)

        async with self.db.session() as session:
            # Create account record
            account_record = ServiceAccountModel(
                account_id=account_id,
                name=name,
                description=description,
                account_type=account_type.value,
                tenant_id=tenant_id,
                role=role.value,
                allowed_projects=allowed_projects or [],
                scopes=scopes or [],
                keys=[initial_key.model_dump()],
                created_at=now,
                created_by=created_by,
            )
            session.add(account_record)

            # Create key mapping record
            key_record = ServiceAccountKeyModel(
                key_hash=key_hash,
                account_id=account_id,
                key_id=key_id,
            )
            session.add(key_record)

        # Build response model
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
            created_at=now.isoformat(),
            created_by=created_by,
        )

        logger.info("Service account created",
                   account_id=account_id,
                   name=name,
                   account_type=account_type.value,
                   tenant_id=tenant_id)

        return account, raw_key

    async def get_account(self, account_id: str) -> Optional[ServiceAccount]:
        """Get service account by ID"""
        async with self.db.session() as session:
            result = await session.execute(
                select(ServiceAccountModel).where(ServiceAccountModel.account_id == account_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return None

            return self._record_to_model(record)

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
        async with self.db.session() as session:
            result = await session.execute(
                select(ServiceAccountModel).where(ServiceAccountModel.account_id == account_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return None

            if name is not None:
                record.name = name
            if description is not None:
                record.description = description
            if role is not None:
                record.role = role.value
            if allowed_projects is not None:
                record.allowed_projects = allowed_projects
            if scopes is not None:
                record.scopes = scopes
            if is_active is not None:
                record.is_active = is_active

            record.updated_at = datetime.now(UTC)

            logger.info("Service account updated",
                       account_id=account_id,
                       is_active=record.is_active)

            return self._record_to_model(record)

    async def delete_account(self, account_id: str) -> bool:
        """Delete service account and all its keys"""
        async with self.db.session() as session:
            # Delete keys first (foreign key constraint)
            await session.execute(
                delete(ServiceAccountKeyModel).where(ServiceAccountKeyModel.account_id == account_id)
            )

            # Delete account
            result = await session.execute(
                delete(ServiceAccountModel).where(ServiceAccountModel.account_id == account_id)
            )

            if result.rowcount > 0:
                logger.warning("Service account deleted", account_id=account_id)
                return True

        return False

    async def list_accounts(
        self,
        tenant_id: Optional[str] = None,
        account_type: Optional[ServiceAccountType] = None,
        is_active: Optional[bool] = None,
        limit: int = 100,
    ) -> List[ServiceAccount]:
        """List service accounts with optional filtering"""
        async with self.db.session() as session:
            query = select(ServiceAccountModel)

            if tenant_id:
                query = query.where(ServiceAccountModel.tenant_id == tenant_id)
            if account_type:
                query = query.where(ServiceAccountModel.account_type == account_type.value)
            if is_active is not None:
                query = query.where(ServiceAccountModel.is_active == is_active)

            query = query.limit(limit)

            result = await session.execute(query)
            records = result.scalars().all()

            return [self._record_to_model(r) for r in records]

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
        async with self.db.session() as session:
            result = await session.execute(
                select(ServiceAccountModel).where(ServiceAccountModel.account_id == account_id)
            )
            record = result.scalar_one_or_none()

            if not record:
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

            # Add to account keys
            keys = list(record.keys or [])
            keys.append(key_info.model_dump())
            record.keys = keys
            record.updated_at = datetime.now(UTC)

            # Create key mapping record
            key_record = ServiceAccountKeyModel(
                key_hash=key_hash,
                account_id=account_id,
                key_id=key_id,
            )
            session.add(key_record)

            logger.info("Service account key created",
                       account_id=account_id,
                       key_id=key_id)

            return raw_key, key_info

    async def revoke_key(self, account_id: str, key_id: str) -> bool:
        """Revoke a service account key"""
        async with self.db.session() as session:
            result = await session.execute(
                select(ServiceAccountModel).where(ServiceAccountModel.account_id == account_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return False

            # Find and remove key from account keys
            keys = list(record.keys or [])
            key_found = False
            for i, key in enumerate(keys):
                if key.get("key_id") == key_id:
                    keys.pop(i)
                    key_found = True
                    break

            if not key_found:
                return False

            record.keys = keys
            record.updated_at = datetime.now(UTC)

            # Delete key mapping
            await session.execute(
                delete(ServiceAccountKeyModel).where(ServiceAccountKeyModel.key_id == key_id)
            )

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

        async with self.db.session() as session:
            # Look up key
            key_result = await session.execute(
                select(ServiceAccountKeyModel).where(ServiceAccountKeyModel.key_hash == key_hash)
            )
            key_record = key_result.scalar_one_or_none()

            if not key_record:
                return None

            account_id = key_record.account_id
            key_id = key_record.key_id

            # Get account
            account_result = await session.execute(
                select(ServiceAccountModel).where(ServiceAccountModel.account_id == account_id)
            )
            record = account_result.scalar_one_or_none()

            if not record or not record.is_active:
                return None

            # Find key and check expiration
            key_info = None
            key_index = -1
            keys = list(record.keys or [])
            for i, key in enumerate(keys):
                if key.get("key_id") == key_id:
                    key_info = key
                    key_index = i
                    break

            if not key_info:
                return None

            # Check expiration
            if key_info.get("expires_at"):
                expires = datetime.fromisoformat(key_info["expires_at"])
                if datetime.now(UTC) > expires:
                    logger.warning("Service account key expired",
                                 account_id=account_id,
                                 key_id=key_id)
                    return None

            # Update last used
            keys[key_index]["last_used"] = datetime.now(UTC).isoformat()
            record.keys = keys
            record.last_active = datetime.now(UTC)
            record.total_requests += 1

            return self._record_to_model(record)

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

    def _record_to_model(self, record: ServiceAccountModel) -> ServiceAccount:
        """Convert database record to Pydantic model"""
        keys = []
        for key_data in (record.keys or []):
            keys.append(ServiceAccountKey(
                key_id=key_data.get("key_id", ""),
                created_at=key_data.get("created_at", ""),
                expires_at=key_data.get("expires_at"),
                last_used=key_data.get("last_used"),
                description=key_data.get("description"),
            ))

        return ServiceAccount(
            account_id=record.account_id,
            name=record.name,
            description=record.description,
            account_type=ServiceAccountType(record.account_type),
            tenant_id=record.tenant_id,
            role=Role(record.role),
            allowed_projects=record.allowed_projects or [],
            scopes=record.scopes or [],
            keys=keys,
            created_at=record.created_at.isoformat() if record.created_at else "",
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
            created_by=record.created_by,
            is_active=record.is_active,
            last_active=record.last_active.isoformat() if record.last_active else None,
            total_requests=record.total_requests or 0,
        )


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

_service_account_manager: Optional[ServiceAccountManager] = None


def init_service_account_manager(db: DatabaseManager) -> ServiceAccountManager:
    """Initialize global service account manager"""
    global _service_account_manager
    _service_account_manager = ServiceAccountManager(db)
    return _service_account_manager


def get_service_account_manager() -> Optional[ServiceAccountManager]:
    """Get global service account manager"""
    return _service_account_manager
