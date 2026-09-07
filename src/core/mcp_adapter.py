"""MCP (Model Context Protocol) server for Contex — the ONLY module (with mcp_bridge)
that imports the mcp SDK. Handlers delegate to ContextEngine/SubscriptionService."""
from __future__ import annotations

import json

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.subscriptions import InMemorySubscriptionBus

from src.core.authz import auth_enabled
from src.core.context_engine import ContextEngine
from src.core.identity import resolve_identity
from src.core.models import DataPublishEvent
from src.core.rbac import Permission


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
    # Redis events.
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
    server = MCPServer(name="contex", version="0.3.0", subscriptions=bus, **auth_kwargs)

    def _get_engine():
        """Resolve the engine, supporting both concrete instances and lazy callables."""
        return engine if isinstance(engine, ContextEngine) else engine()

    @server.tool(name="contex_query", description="Semantic query over a project's context (stateless).")
    async def contex_query(project_id: str, query: str, top_k: int = 5, threshold: float | None = None) -> str:
        _enforce(Permission.QUERY_DATA, project_id=project_id)
        e = _get_engine()
        matches = await e.query_project_data(project_id, query, top_k=top_k, threshold=threshold)
        return json.dumps({"query": query, "matches": matches})

    @server.tool(name="contex_create_subscription",
                 description="Create a live subscription; returns its resource URI to subscribe to.")
    async def contex_create_subscription(project_id: str, needs: list[str],
                                         top_k: int = 5, threshold: float | None = None) -> str:
        _enforce(Permission.QUERY_DATA, project_id=project_id)
        e = _get_engine()
        sub_id = await e.subscriptions.create(project_id, needs, top_k=top_k, threshold=threshold)
        return json.dumps({"subscription_id": sub_id, "resource_uri": f"contex://subscriptions/{sub_id}"})

    @server.tool(name="contex_delete_subscription", description="Delete a subscription.")
    async def contex_delete_subscription(subscription_id: str) -> str:
        _enforce(Permission.QUERY_DATA)
        e = _get_engine()
        await e.subscriptions.delete(subscription_id)
        return json.dumps({"deleted": subscription_id})

    @server.resource("contex://subscriptions/{id}", name="subscription",
                     description="A subscription's current matched context bundle.",
                     mime_type="application/json")
    async def read_subscription(id: str) -> str:
        _enforce(Permission.QUERY_DATA)
        e = _get_engine()
        return json.dumps(await e.subscriptions.get_bundle(id))

    @server.tool(name="contex_publish", description="Publish/update context data for a project.")
    async def contex_publish(project_id: str, data_key: str, data: dict, data_format: str = "json") -> str:
        _enforce(Permission.PUBLISH_DATA, project_id=project_id)
        e = _get_engine()
        seq = await e.publish_data(DataPublishEvent(
            project_id=project_id, data_key=data_key, data=data, data_format=data_format,
        ))
        return json.dumps({"published": data_key, "sequence": str(seq)})

    return server, bus
