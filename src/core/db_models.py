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
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class TenantScopedMixin:
    """Standard tenant_id column for tables that reference a tenant.
    Subclasses may override _tenant_ondelete (default RESTRICT)."""
    _tenant_ondelete = "RESTRICT"

    @declared_attr
    def tenant_id(cls) -> Mapped[str]:
        return mapped_column(
            String(255),
            ForeignKey("tenants.tenant_id", ondelete=cls._tenant_ondelete),
            nullable=False,
            server_default="default",
        )


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


class APIKey(TenantScopedMixin, Base):
    """API Key model."""

    __tablename__ = "api_keys"

    key_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    scopes: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
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


class Event(TenantScopedMixin, Base):
    """Event model - event sourcing table."""

    __tablename__ = "events"

    _tenant_ondelete = "CASCADE"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    data: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    data_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="api"
    )
    actor_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    actor_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    actor_ip: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_events_project_sequence", "project_id", "sequence", unique=True),
        Index("idx_events_project_created", "project_id", "created_at"),
        Index("idx_events_tenant", "tenant_id"),
        Index(
            "idx_events_project_data_key_sequence",
            "project_id",
            "data_key",
            sequence.desc(),
        ),
    )


class EventSequenceCounter(Base):
    """Per-project event sequence counter.

    Backs atomic, race-free sequence assignment for the event log (#104). The
    event store bumps a project's counter with a single
    ``INSERT ... ON CONFLICT (project_id) DO UPDATE ... RETURNING`` statement,
    which row-locks the counter and serialises concurrent publishes to the same
    project while keeping sequences per-project, monotonic, and starting from 1.
    Kept in agreement with alembic migration 006 (which is the schema source).
    """

    __tablename__ = "event_sequence_counters"

    project_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
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
    # sha256 hex of the embedded text; lets ingest skip re-embedding unchanged nodes (#223).
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    embedding = mapped_column(Vector(768), nullable=False)  # 768-dim for thenlper/gte-base
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_embeddings_project", "project_id"),
        Index("idx_embeddings_project_node_key", "project_id", "node_key", unique=True),
        Index("idx_embeddings_project_data_key", "project_id", "data_key"),
        # HNSW ANN index for vector cosine similarity search. Without this, the
        # create_all path (and every test DB) would fall back to a sequential scan
        # for semantic search. Kept in sync with alembic migration 001.
        Index(
            "idx_embeddings_vector",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )


class Symbol(Base):
    """A name a node defines or references — the index for cross-file linking.

    ``role='def'`` means the node defines the name (a function/class/method);
    ``role='ref'`` means it mentions it (an import or call target). A cross-file
    "edge" is a read-time equality join — ``ref.name == def.name`` within a
    project — so there is no edge table and no ingest-ordering problem: a ref
    whose def hasn't arrived yet simply doesn't resolve until it does. General by
    design: any named cross-reference (markdown anchors, JSON ``$ref``) can reuse
    this table; code is just the first producer. Rows are rewritten per source on
    every re-ingest (delete by ``(project_id, data_key)`` then insert).
    """

    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    data_key: Mapped[str] = mapped_column(String(255), nullable=False)
    node_key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # "def" | "ref"

    __table_args__ = (
        # def lookup in the link join: resolve a ref name to defining nodes.
        Index("idx_symbols_project_name_role", "project_id", "name", "role"),
        # gather the refs of the already-matched nodes.
        Index("idx_symbols_project_node_key", "project_id", "node_key"),
        # delete-by-source on re-ingest.
        Index("idx_symbols_project_data_key", "project_id", "data_key"),
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


class Subscription(TenantScopedMixin, Base):
    """A persistent semantic subscription: needs + a materialized matched bundle."""

    __tablename__ = "subscriptions"

    subscription_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    needs: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    scope: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    top_k: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Materialized matches, shape = SemanticDataMatcher.match_agent_needs output.
    bundle: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'"))
    bundle_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_subscriptions_project", "project_id"),
    )
