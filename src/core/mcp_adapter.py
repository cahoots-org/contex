"""MCP (Model Context Protocol) server for Contex — the ONLY module (with mcp_bridge)
that imports the mcp SDK. Handlers delegate to ContextEngine/SubscriptionService."""
from __future__ import annotations

import json

from mcp.server import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus

from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


def build_mcp_server(engine):
    """Build the Contex MCP server bound to a ContextEngine. Returns (server, bus).

    ``engine`` may be either a ``ContextEngine`` instance (concrete, backward
    compatible) or a zero-argument callable that returns a ``ContextEngine`` at
    call time (lazy accessor, used when the engine is not yet available at
    module-import time).  All tool/resource handlers resolve the engine lazily so
    that either form works correctly at runtime.
    """
    bus = InMemorySubscriptionBus()
    server = MCPServer(name="contex", version="0.3.0", subscriptions=bus)

    def _get_engine():
        """Resolve the engine, supporting both concrete instances and lazy callables."""
        return engine if isinstance(engine, ContextEngine) else engine()

    @server.tool(name="contex_query", description="Semantic query over a project's context (stateless).")
    async def contex_query(project_id: str, query: str, top_k: int = 5, threshold: float | None = None) -> str:
        e = _get_engine()
        matches = await e.query_project_data(project_id, query, top_k=top_k, threshold=threshold)
        return json.dumps({"query": query, "matches": matches})

    @server.tool(name="contex_create_subscription",
                 description="Create a live subscription; returns its resource URI to subscribe to.")
    async def contex_create_subscription(project_id: str, needs: list[str],
                                         top_k: int = 5, threshold: float | None = None) -> str:
        e = _get_engine()
        sub_id = await e.subscriptions.create(project_id, needs, top_k=top_k, threshold=threshold)
        return json.dumps({"subscription_id": sub_id, "resource_uri": f"contex://subscriptions/{sub_id}"})

    @server.tool(name="contex_delete_subscription", description="Delete a subscription.")
    async def contex_delete_subscription(subscription_id: str) -> str:
        e = _get_engine()
        await e.subscriptions.delete(subscription_id)
        return json.dumps({"deleted": subscription_id})

    @server.resource("contex://subscriptions/{id}", name="subscription",
                     description="A subscription's current matched context bundle.",
                     mime_type="application/json")
    async def read_subscription(id: str) -> str:
        e = _get_engine()
        return json.dumps(await e.subscriptions.get_bundle(id))

    @server.tool(name="contex_publish", description="Publish/update context data for a project.")
    async def contex_publish(project_id: str, data_key: str, data: dict, data_format: str = "json") -> str:
        e = _get_engine()
        seq = await e.publish_data(DataPublishEvent(
            project_id=project_id, data_key=data_key, data=data, data_format=data_format,
        ))
        return json.dumps({"published": data_key, "sequence": str(seq)})

    return server, bus
