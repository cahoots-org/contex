"""Publish, subscribe, and watch a subscription stay current, over MCP.

Runs the full Contex loop against a running server: a producer publishes data,
an agent subscribes to a plain-English need that shares no keywords with the
data, reads the matched context, and reads it again after the data changes
without issuing a second query.

    pip install "mcp>=2,<3"
    python examples/mcp_quickstart.py

Point at a non-default server with CONTEX_MCP_URL, e.g.
CONTEX_MCP_URL=http://localhost:8011/mcp.
"""

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CONTEX_MCP_URL = os.getenv("CONTEX_MCP_URL", "http://localhost:8001/mcp")
PROJECT_ID = "examples-quickstart"


def _tool_json(result):
    """Parse the JSON payload a Contex tool returns as its text content."""
    return json.loads(result.content[0].text)


def _resource_json(result):
    """Parse the JSON payload of a resource read."""
    return json.loads(result.contents[0].text)


async def main():
    async with streamable_http_client(CONTEX_MCP_URL) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()

            print("1. Publish a database DSN under an arbitrary key.")
            await session.call_tool(
                "contex_publish",
                {
                    "project_id": PROJECT_ID,
                    "data_key": "pg_dsn",
                    "data": {"engine": "postgres", "host": "db.internal", "port": 5432, "pool": 20},
                },
            )

            print("2. Subscribe to a need in plain English (no shared keywords).")
            sub = _tool_json(
                await session.call_tool(
                    "contex_create_subscription",
                    {"project_id": PROJECT_ID, "needs": ["how the service reaches its datastore"]},
                )
            )
            resource_uri = sub["resource_uri"]
            print(f"   subscription: {resource_uri}")

            print("3. Read the matched context. Contex matched by meaning.")
            bundle = _resource_json(await session.read_resource(resource_uri))
            print(json.dumps(bundle, indent=2))

            print("4. Change the underlying data.")
            await session.call_tool(
                "contex_publish",
                {
                    "project_id": PROJECT_ID,
                    "data_key": "pg_dsn",
                    "data": {"engine": "postgres", "host": "db-replica.internal", "port": 5432, "pool": 50},
                },
            )

            print("5. Read the subscription again. It is already current; no re-query.")
            bundle = _resource_json(await session.read_resource(resource_uri))
            print(json.dumps(bundle, indent=2))

            await session.call_tool("contex_delete_subscription", {"subscription_id": sub["subscription_id"]})


if __name__ == "__main__":
    asyncio.run(main())
