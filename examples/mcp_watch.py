"""Receive a live push over MCP when a subscription's context changes.

An agent creates a subscription and listens on its resource with
subscriptions/listen. A second client then publishes a change, and the server
pushes a resource-updated event to the agent, which re-reads the resource. The
agent never polls or queries again.

    pip install "mcp>=2,<3"
    python examples/mcp_watch.py

Point at a non-default server with CONTEX_MCP_URL, e.g.
CONTEX_MCP_URL=http://localhost:8011/mcp.
"""

import asyncio
import json
import os

from mcp import Client

CONTEX_MCP_URL = os.getenv("CONTEX_MCP_URL", "http://localhost:8001/mcp")
PROJECT_ID = "examples-watch"


async def publish(note: str) -> None:
    """Publish a change from a separate client, like another agent would."""
    async with Client(CONTEX_MCP_URL) as producer:
        await producer.call_tool("contex_publish", {
            "project_id": PROJECT_ID,
            "data_key": "change:charge",
            "data": {"file": "payments/charge.py", "note": note},
        })


async def main():
    await publish("charge() retries declined cards once")

    async with Client(CONTEX_MCP_URL) as agent:
        sub = json.loads((await agent.call_tool("contex_create_subscription", {
            "project_id": PROJECT_ID, "needs": ["recent changes to the payment code"],
        })).content[0].text)
        uri = sub["resource_uri"]

        async with agent.listen(resource_subscriptions=[uri]) as events:
            print(f"agent: listening on {uri}")
            print("producer: charge() now requires a currency argument")
            await publish("charge() now requires a currency argument")

            event = await asyncio.wait_for(anext(aiter(events)), timeout=30)
            print(f"agent: server pushed an update for {event.uri}")

        bundle = json.loads((await agent.read_resource(uri)).contents[0].text)
        for need, matches in bundle.items():
            for match in matches:
                print(f"agent: [{need}] {json.dumps(match['data'])}")

        await agent.call_tool("contex_delete_subscription", {"subscription_id": sub["subscription_id"]})


if __name__ == "__main__":
    asyncio.run(main())
