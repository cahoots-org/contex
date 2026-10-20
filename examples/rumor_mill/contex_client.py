"""Async Contex MCP client used by the villagers' minds."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

from mcp import Client

MAX_CONCURRENT_CALLS = 8


@dataclass(frozen=True)
class Match:
    data_key: str
    data: dict


Bundle = dict[str, list[Match]]


class SubscriptionGone(Exception):
    """The subscription expired and must be created again."""


def new_matches(seen: set[str], bundle: Bundle) -> list[Match]:
    """Matches whose data_key is not in seen, once each, in bundle order."""
    found: dict[str, Match] = {}
    for matches in bundle.values():
        for match in matches:
            if match.data_key not in seen:
                found.setdefault(match.data_key, match)
    return list(found.values())


class ContexClient:
    """Contex tools over one shared MCP session, plus per-call listen sessions."""

    def __init__(self, url: str):
        self.url = url
        self._client: Client | None = None
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

    async def __aenter__(self) -> "ContexClient":
        self._client = await Client(self.url).__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.__aexit__(*exc)

    async def _tool(self, name: str, args: dict) -> dict:
        async with self._slots:
            result = await self._client.call_tool(name, args)
        return json.loads(result.content[0].text)

    async def publish(self, project_id: str, data_key: str, data: dict) -> None:
        await self._tool("contex_publish", {"project_id": project_id, "data_key": data_key, "data": data})

    async def subscribe(self, project_id: str, needs: list[str], *, max_age_seconds: int,
                        top_k: int, threshold: float | None = None) -> str:
        args = {"project_id": project_id, "needs": needs, "top_k": top_k,
                "max_age_seconds": max_age_seconds}
        if threshold is not None:
            args["threshold"] = threshold
        return (await self._tool("contex_create_subscription", args))["resource_uri"]

    async def read(self, uri: str) -> Bundle:
        try:
            async with self._slots:
                result = await self._client.read_resource(uri)
        except Exception as e:
            if "Subscription expired" in str(e):
                raise SubscriptionGone(uri) from e
            raise
        raw = json.loads(result.contents[0].text)
        return {need: [Match(m["data_key"], m["data"]) for m in matches] for need, matches in raw.items()}

    async def query(self, project_id: str, query: str, top_k: int = 3) -> list[Match]:
        found = await self._tool("contex_query", {"project_id": project_id, "query": query, "top_k": top_k})
        return [Match(m["data_key"], m["data"]) for m in found["matches"]]

    async def listen(self, uri: str) -> AsyncIterator[None]:
        """Yield once per push on uri, in its own session, until the stream closes."""
        async with Client(self.url) as session:
            async with session.listen(resource_subscriptions=[uri]) as events:
                async for _ in events:
                    try:
                        yield
                    except GeneratorExit:
                        # Closing the generator must unwind the sessions normally, not as a task-group error.
                        return
