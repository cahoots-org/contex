"""One-shot semantic query over a project's context, over MCP (contex_query).

Stateless retrieval with no subscription and no registration: publish a few
facts, then ask a question in plain English and get the matches ranked by
meaning.

    pip install "mcp>=2,<3"
    python examples/query.py

Point at a non-default server with CONTEX_MCP_URL, e.g.
CONTEX_MCP_URL=http://localhost:8011/mcp.
"""

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CONTEX_MCP_URL = os.getenv("CONTEX_MCP_URL", "http://localhost:8001/mcp")
PROJECT_ID = "examples-query"

FACTS = {
    "oncall": {"team": "payments", "primary": "Dana", "escalation": "page the SRE lead after 15 minutes"},
    "deploy": {"strategy": "blue-green", "approvals": 2, "freeze": "no deploys on Fridays"},
    "datastore": {"engine": "postgres", "host": "db.internal", "backups": "hourly WAL shipping"},
}


def _tool_json(result):
    return json.loads(result.content[0].text)


async def main():
    async with streamable_http_client(CONTEX_MCP_URL) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()

            for key, data in FACTS.items():
                await session.call_tool(
                    "contex_publish",
                    {"project_id": PROJECT_ID, "data_key": key, "data": data},
                )

            question = "who gets paged when the database goes down"
            print(f"query: {question}\n")
            result = _tool_json(
                await session.call_tool(
                    "contex_query",
                    {"project_id": PROJECT_ID, "query": question, "top_k": 3},
                )
            )
            for match in result["matches"]:
                print(f"  {match['data_key']}  (score {match['similarity']:.4f})")
                print(f"    {match['description']}")


if __name__ == "__main__":
    asyncio.run(main())
