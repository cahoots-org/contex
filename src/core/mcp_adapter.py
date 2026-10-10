"""MCP (Model Context Protocol) server for Contex — the ONLY module (with mcp_bridge)
that imports the mcp SDK. Handlers delegate to ContextEngine/SubscriptionService."""
from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Optional

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.subscriptions import InMemorySubscriptionBus

from src.core.authz import auth_enabled
from src.core.context_engine import ContextEngine
from src.core.identity import resolve_identity
from src.core.limits import MAX_BATCH_SIZE, check_batch_size
from src.core.models import DataPublishEvent
from src.core.rate_limiter import RateLimitConfig, RateLimiter, _env_int
from src.core.rbac import Permission
from src.core.recency import window_start
from src.core.version import VERSION


def _parse_timestamp(value: Optional[str], field: str = "since") -> Optional[datetime]:
    """Parse an ISO-8601 timestamp argument, raising ValueError on bad input."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"Invalid '{field}' value {value!r}; expected ISO-8601 (e.g. 2025-01-01T00:00:00Z)"
        )


# Formats published as base64 strings, since MCP arguments are JSON.
_BINARY_FORMATS = frozenset({"pdf", "docx", "image"})


def _item_data(data, data_format: str):
    """Decode a base64 document to bytes; other data passes through."""
    if data_format not in _BINARY_FORMATS:
        return data
    try:
        return base64.b64decode(data, validate=True)
    except (TypeError, ValueError):
        raise ToolError(f"data for a {data_format} item must be a base64 string")


def _check_max_age(max_age_seconds: Optional[int]) -> None:
    if max_age_seconds is not None and max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a positive number of seconds")


async def _throttle(engine, bucket: str, limit_env: str, default_limit: int) -> None:
    """Per-principal rate limit for a write tool.

    MCP multiplexes every tool over one HTTP path, so the transport middleware
    can't throttle one tool and not another — the per-tool decision lives here.
    ``limit_env`` <= 0 disables throttling (the bulk-ingest tier is exempt by
    default); set it to impose a ceiling. Raises ``ToolError`` so the backoff
    hint survives to the client (a plain exception would be redacted).
    """
    limit = _env_int(limit_env, default_limit)
    if limit <= 0:
        return
    tok = get_access_token()
    principal = tok.client_id if tok else "anonymous"
    window = RateLimitConfig.from_env().window
    allowed, info = await RateLimiter(engine.db).check_rate_limit(
        f"{principal}:{bucket}", limit, window
    )
    if not allowed:
        raise ToolError(f"rate_limit_exceeded retry_after={info['retry_after']}")


def _enforce(permission, project_id=None):
    """Default-deny per-tool check. No-op when auth is off (demo parity)."""
    if not auth_enabled():
        return
    tok = get_access_token()
    if tok is None or permission.value not in tok.scopes:
        raise PermissionError("Permission denied")
    if project_id is not None:
        projects = (tok.claims or {}).get("projects") or []
        if projects and project_id not in projects:
            raise PermissionError("Permission denied")


async def _enforce_subscription_project(engine, subscription_id, tenant_id):
    """Apply the caller's project scope to the subscription's own project."""
    project_id = await engine.subscriptions.project_of(subscription_id, tenant_id=tenant_id)
    _enforce(Permission.QUERY_DATA, project_id=project_id)


class ApiKeyVerifier(TokenVerifier):
    """Resolve a Contex API key into an MCP AccessToken. DB is resolved lazily."""

    def __init__(self, db_accessor):
        self._db_accessor = db_accessor  # zero-arg callable → DatabaseManager

    async def verify_token(self, token: str) -> AccessToken | None:
        identity = await resolve_identity(self._db_accessor(), token)
        if identity is None:
            return None
        return AccessToken(
            token=token,
            client_id=identity.key_id,
            scopes=[p.value for p in identity.scopes],
            claims={
                "role": identity.role.value if identity.role else None,
                "tenant_id": identity.tenant_id,
                "projects": list(identity.projects),
            },
        )


def build_mcp_server(engine, db_accessor=None):
    """Build the Contex MCP server bound to a ContextEngine. Returns (server, bus).

    ``engine`` may be either a ``ContextEngine`` instance (concrete, backward
    compatible) or a zero-argument callable that returns a ``ContextEngine`` at
    call time (lazy accessor, used when the engine is not yet available at
    module-import time).  All tool/resource handlers resolve the engine lazily so
    that either form works correctly at runtime.
    """
    # InMemorySubscriptionBus is MCP 2.0's in-process fan-out mechanism for
    # resources/updated notifications to currently-connected MCP client sessions.
    # It is NOT subscription persistence — durable subscription state (needs +
    # materialized bundle) lives in the Subscription DB table and survives restarts.
    # The bus only routes live notifications and is re-established when clients
    # reconnect. It is multi-replica-safe because the bridge is driven by shared
    # Postgres notifications.
    bus = InMemorySubscriptionBus()
    auth_kwargs = {}
    if auth_enabled() and db_accessor is not None:
        auth_kwargs = dict(
            token_verifier=ApiKeyVerifier(db_accessor),
            auth=AuthSettings(
                issuer_url="https://contex.local",  # required by pydantic; unused in this path
                resource_server_url=None,            # keeps us off RFC 9728 discovery
                required_scopes=None,               # per-tool checks live in handlers
            ),
        )
    server = MCPServer(name="contex", version=VERSION, subscriptions=bus, **auth_kwargs)

    def _get_engine():
        """Resolve the engine, supporting both concrete instances and lazy callables."""
        return engine if isinstance(engine, ContextEngine) else engine()

    @server.tool(name="contex_query",
                 description="Semantic query over a project's context (stateless). "
                             "Pass since (ISO-8601) to match only data created or updated on or after that time, "
                             "or max_age_seconds to match only data from the last that many seconds.")
    async def contex_query(project_id: str, query: str, top_k: int = 5,
                           threshold: float | None = None, since: str | None = None,
                           max_age_seconds: int | None = None) -> str:
        _enforce(Permission.QUERY_DATA, project_id=project_id)
        _check_max_age(max_age_seconds)
        e = _get_engine()
        matches = await e.query_project_data(
            project_id, query, top_k=top_k, threshold=threshold,
            since=window_start(_parse_timestamp(since), max_age_seconds),
        )
        return json.dumps({"query": query, "matches": matches})

    @server.tool(name="contex_create_subscription",
                 description="Create a live subscription; returns its resource URI to subscribe to. "
                             "It expires when neither read nor re-created for a while; calling again with the "
                             "same arguments resumes it with the same URI. "
                             "Pass since (ISO-8601) to keep the bundle scoped to data created or updated on or after that time. "
                             "Pass max_age_seconds for a sliding window: items drop out of the bundle once older than that, "
                             "checked every few minutes.")
    async def contex_create_subscription(project_id: str, needs: list[str],
                                         top_k: int = 5, threshold: float | None = None,
                                         since: str | None = None, max_age_seconds: int | None = None) -> str:
        _enforce(Permission.QUERY_DATA, project_id=project_id)
        tok = get_access_token()
        tid = (tok.claims or {}).get("tenant_id") if tok else None
        # Validate eagerly so a bad value fails the call, not reconcile.
        _parse_timestamp(since)
        _check_max_age(max_age_seconds)
        scope = {k: v for k, v in (("since", since), ("max_age_seconds", max_age_seconds)) if v is not None} or None
        e = _get_engine()
        sub_id = await e.subscriptions.create(
            project_id, needs, tenant_id=tid, top_k=top_k, threshold=threshold, scope=scope
        )
        return json.dumps({"subscription_id": sub_id, "resource_uri": f"contex://subscriptions/{sub_id}"})

    @server.tool(name="contex_delete_subscription", description="Delete a subscription.")
    async def contex_delete_subscription(subscription_id: str) -> str:
        _enforce(Permission.QUERY_DATA)
        tok = get_access_token()
        tid = (tok.claims or {}).get("tenant_id") if tok else None
        e = _get_engine()
        await _enforce_subscription_project(e, subscription_id, tid)
        await e.subscriptions.delete(subscription_id, tenant_id=tid)
        return json.dumps({"deleted": subscription_id})

    @server.resource("contex://subscriptions/{id}", name="subscription",
                     description="A subscription's current matched context bundle.",
                     mime_type="application/json")
    async def read_subscription(id: str) -> str:
        _enforce(Permission.QUERY_DATA)
        tok = get_access_token()
        tid = (tok.claims or {}).get("tenant_id") if tok else None
        e = _get_engine()
        await _enforce_subscription_project(e, id, tid)
        return json.dumps(await e.subscriptions.get_bundle(id, tenant_id=tid))

    @server.tool(name="contex_publish",
                 description="Publish/update context data for a project. "
                             "Pass published_at (ISO-8601) when the source changed it, e.g. for backfilled history; "
                             "time windows use it instead of ingest time.")
    async def contex_publish(project_id: str, data_key: str, data: dict, data_format: str = "json",
                             published_at: str | None = None) -> str:
        _enforce(Permission.PUBLISH_DATA, project_id=project_id)
        e = _get_engine()
        await _throttle(e, "publish", "RATE_LIMIT_PUBLISH", 60)
        seq = await e.publish_data(DataPublishEvent(
            project_id=project_id, data_key=data_key, data=data, data_format=data_format,
            published_at=_parse_timestamp(published_at, "published_at"),
        ), source='mcp')
        return json.dumps({"published": data_key, "sequence": str(seq)})

    @server.tool(name="contex_publish_batch",
                 description="Publish/update many context items for a project in one call. "
                             "items is a list of {data_key, data, data_format?, published_at?}; for data_format "
                             "pdf, docx or image, data is the file as a base64 string. "
                             "origin names the source stream (e.g. s3:bucket/prefix) so contex_list_keys can "
                             "list what it published.")
    async def contex_publish_batch(project_id: str, items: list[dict], origin: str | None = None) -> str:
        _enforce(Permission.PUBLISH_DATA, project_id=project_id)
        try:
            check_batch_size(items, "items")
        except ValueError as exc:
            raise ValueError(str(exc))
        e = _get_engine()
        await _throttle(e, "ingest", "RATE_LIMIT_INGEST", 0)
        sequences = await e.publish_data_batch([
            DataPublishEvent(
                project_id=project_id,
                data_key=item["data_key"],
                data=_item_data(item["data"], item.get("data_format", "json")),
                data_format=item.get("data_format", "json"),
                published_at=_parse_timestamp(item.get("published_at"), "published_at"),
            )
            for item in items
        ], source='mcp', origin=origin)
        return json.dumps({"published": len(sequences)})

    @server.tool(name="contex_list_keys",
                 description="List the data_keys an origin last published, in order, up to limit per page. "
                             "Pass the returned next as after to fetch the following page; next is null on the last page.")
    async def contex_list_keys(project_id: str, origin: str, after: str | None = None, limit: int = 1000) -> str:
        _enforce(Permission.PUBLISH_DATA, project_id=project_id)
        limit = max(1, min(limit, MAX_BATCH_SIZE))
        keys = await _get_engine().list_keys(project_id, origin, after=after, limit=limit)
        return json.dumps({"keys": keys, "next": keys[-1] if len(keys) == limit else None})

    @server.tool(name="contex_delete",
                 description="Delete context documents from a project by data_key (the `document` "
                             "field of a query match). Returns which keys were deleted and which "
                             "were not found.")
    async def contex_delete(project_id: str, data_keys: list[str]) -> str:
        _enforce(Permission.PUBLISH_DATA, project_id=project_id)
        check_batch_size(data_keys, "data_keys")
        e = _get_engine()
        await _throttle(e, "ingest", "RATE_LIMIT_INGEST", 0)
        return json.dumps(await e.delete_data(project_id, data_keys, source="mcp"))

    return server, bus
