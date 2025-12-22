"""
SQLAlchemy ORM Models for Contex

Defines all database tables and their relationships.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class Tenant(Base):
    """Tenant model - represents a customer/organization."""

    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="free")
    quotas: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    settings: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    owner_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    billing_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    usage: Mapped[Optional["TenantUsage"]] = relationship(back_populates="tenant", uselist=False)
    projects: Mapped[List["TenantProject"]] = relationship(back_populates="tenant")
    api_keys: Mapped[List["APIKey"]] = relationship(back_populates="tenant")
    service_accounts: Mapped[List["ServiceAccount"]] = relationship(back_populates="tenant")
    webhook_endpoints: Mapped[List["WebhookEndpoint"]] = relationship(back_populates="tenant")
    agents: Mapped[List["AgentRegistration"]] = relationship(back_populates="tenant")


class TenantUsage(Base):
    """Tenant usage tracking."""

    __tablename__ = "tenant_usage"

    tenant_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), primary_key=True
    )
    projects_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agents_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    api_keys_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events_this_month: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_used_mb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="usage")


class TenantProject(Base):
    """Tenant-Project relationship."""

    __tablename__ = "tenant_projects"

    tenant_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="projects")


class APIKey(Base):
    """API Key model."""

    __tablename__ = "api_keys"

    key_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    scopes: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    tenant_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("tenants.tenant_id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    tenant: Mapped[Optional["Tenant"]] = relationship(back_populates="api_keys")
    role: Mapped[Optional["APIKeyRole"]] = relationship(back_populates="api_key", uselist=False)

    __table_args__ = (
        Index("idx_api_keys_hash", "key_hash"),
    )


class APIKeyRole(Base):
    """API Key Role Assignment."""

    __tablename__ = "api_key_roles"

    key_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("api_keys.key_id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="readonly")
    projects: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)

    # Relationships
    api_key: Mapped["APIKey"] = relationship(back_populates="role")


class ServiceAccount(Base):
    """Service Account model."""

    __tablename__ = "service_accounts"

    account_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    account_type: Mapped[str] = mapped_column(String(50), nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("tenants.tenant_id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="readonly")
    allowed_projects: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    scopes: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    keys: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_active: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    tenant: Mapped[Optional["Tenant"]] = relationship(back_populates="service_accounts")
    key_mappings: Mapped[List["ServiceAccountKey"]] = relationship(back_populates="account")


class ServiceAccountKey(Base):
    """Service Account Key mapping."""

    __tablename__ = "service_account_keys"

    key_hash: Mapped[str] = mapped_column(String(255), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("service_accounts.account_id", ondelete="CASCADE"), nullable=False
    )
    key_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Relationships
    account: Mapped["ServiceAccount"] = relationship(back_populates="key_mappings")


class Event(Base):
    """Event model - event sourcing table."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    data: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_events_project_sequence", "project_id", "sequence", unique=True),
        Index("idx_events_project_created", "project_id", "created_at"),
        Index("idx_events_tenant", "tenant_id"),
    )


class Snapshot(Base):
    """Snapshot model - project state snapshots."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    data: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metadata_: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_snapshots_project", "project_id"),
        Index("idx_snapshots_project_sequence", "project_id", "sequence", unique=True),
    )


class Embedding(Base):
    """Embedding model - semantic vector storage with pgvector."""

    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    data_key: Mapped[str] = mapped_column(String(255), nullable=False)
    node_key: Mapped[str] = mapped_column(String(255), nullable=False)
    node_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    node_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    data_original: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data_format: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    embedding = mapped_column(Vector(384), nullable=False)  # 384-dim for all-MiniLM-L6-v2
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_embeddings_project", "project_id"),
        Index("idx_embeddings_project_node_key", "project_id", "node_key", unique=True),
        Index("idx_embeddings_project_data_key", "project_id", "data_key"),
    )


class AuditEvent(Base):
    """Audit Event model."""

    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    actor_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    actor_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    actor_ip: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    actor_user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    resource_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    request_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    endpoint: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    method: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    before_state: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("idx_audit_tenant", "tenant_id"),
        Index("idx_audit_actor", "actor_id"),
        Index("idx_audit_type", "event_type"),
        Index("idx_audit_timestamp", "timestamp"),
    )


class WebhookEndpoint(Base):
    """Webhook Endpoint model."""

    __tablename__ = "webhook_endpoints"

    endpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    categories: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    project_ids: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant: Mapped[Optional["Tenant"]] = relationship(back_populates="webhook_endpoints")
    deliveries: Mapped[List["WebhookDelivery"]] = relationship(back_populates="endpoint")

    __table_args__ = (
        Index("idx_webhook_tenant", "tenant_id"),
        Index("idx_webhook_active", "is_active"),
    )


class WebhookDelivery(Base):
    """Webhook Delivery log."""

    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("webhook_endpoints.endpoint_id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Relationships
    endpoint: Mapped["WebhookEndpoint"] = relationship(back_populates="deliveries")

    __table_args__ = (
        Index("idx_webhook_delivery_endpoint", "endpoint_id"),
    )


class RateLimitEntry(Base):
    """Rate Limit Entry - sliding window rate limiting."""

    __tablename__ = "rate_limit_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rate_key: Mapped[str] = mapped_column(String(512), nullable=False)
    request_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_rate_limit_key_time", "rate_key", "request_time"),
    )


class AgentRegistration(Base):
    """Agent Registration model."""

    __tablename__ = "agent_registrations"

    agent_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=True
    )
    needs: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    notification_method: Mapped[str] = mapped_column(String(20), nullable=False, default="redis")
    response_format: Mapped[str] = mapped_column(String(20), nullable=False, default="json")
    notification_channel: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    webhook_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    webhook_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data_keys: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    last_sequence: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    data: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant: Mapped[Optional["Tenant"]] = relationship(back_populates="agents")

    __table_args__ = (
        Index("idx_agent_project", "project_id"),
        Index("idx_agent_tenant", "tenant_id"),
        Index("idx_agent_last_seen", "last_seen"),
    )
