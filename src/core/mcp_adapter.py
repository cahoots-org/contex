"""MCP (Model Context Protocol) server for Contex — the ONLY module (with mcp_bridge)
that imports the mcp SDK. Handlers delegate to ContextEngine/SubscriptionService."""
from __future__ import annotations

import json

from mcp.server import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus


def build_mcp_server(engine):
    """Build the Contex MCP server bound to a ContextEngine. Returns (server, bus)."""
    bus = InMemorySubscriptionBus()
    server = MCPServer(name="contex", version="0.3.0", subscriptions=bus)

    @server.tool(name="contex_query", description="Semantic query over a project's context (stateless).")
    async def contex_query(project_id: str, query: str, top_k: int = 5, threshold: float | None = None) -> str:
        matches = await engine.query_project_data(project_id, query, top_k=top_k, threshold=threshold)
        return json.dumps({"query": query, "matches": matches})

    @server.tool(name="contex_create_subscription",
                 description="Create a live subscription; returns its resource URI to subscribe to.")
    async def contex_create_subscription(project_id: str, needs: list[str],
                                         top_k: int = 5, threshold: float | None = None) -> str:
        sub_id = await engine.subscriptions.create(project_id, needs, top_k=top_k, threshold=threshold)
        return json.dumps({"subscription_id": sub_id, "resource_uri": f"contex://subscriptions/{sub_id}"})

    @server.tool(name="contex_delete_subscription", description="Delete a subscription.")
    async def contex_delete_subscription(subscription_id: str) -> str:
        await engine.subscriptions.delete(subscription_id)
        return json.dumps({"deleted": subscription_id})

    return server, bus
