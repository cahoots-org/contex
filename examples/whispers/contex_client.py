"""Async Contex MCP client used by the villagers' minds."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from mcp import Client

MAX_CONCURRENT_CALLS = 8
RECONNECT_SECONDS = 1.0
MAX_RECONNECT_SECONDS = 30.0

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Match:
    data_key: str
    data: dict
    similarity: float | None = None
    # How far the similarity clears the matching need's threshold, at its best.
    clearance: float | None = None


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
    """Contex tools over one shared MCP session, plus per-call listen sessions.

    The shared session lives in its own task and reconnects with backoff when it drops,
    so a lost connection fails only the calls in flight.
    """

    def __init__(self, url: str):
        self.url = url
        self._client: Client | None = None
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_CALLS)
        self._connected = asyncio.Event()
        self._session: asyncio.Task | None = None

    async def __aenter__(self) -> "ContexClient":
        self._session = asyncio.create_task(self._keep_session())
        ready = asyncio.create_task(self._connected.wait())
        await asyncio.wait({self._session, ready}, return_when=asyncio.FIRST_COMPLETED)
        ready.cancel()
        if self._session.done():
            self._session.result()
        return self

    async def __aexit__(self, *exc) -> None:
        self._session.cancel()
        await asyncio.gather(self._session, return_exceptions=True)

    async def _keep_session(self) -> None:
        """Hold the shared session, reopening it after a drop; the first connect's failure propagates."""
        delay = RECONNECT_SECONDS
        while True:
            try:
                async with Client(self.url) as client:
                    self._client = client
                    self._connected.set()
                    delay = RECONNECT_SECONDS
                    await asyncio.Event().wait()
            except Exception as e:
                if not self._connected.is_set():
                    raise
                cause = e.exceptions[0] if isinstance(e, ExceptionGroup) else e
                log.warning("Contex session lost (%s); reconnecting in %.0fs", type(cause).__name__, delay)
            self._client = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_SECONDS)

    def _session_client(self) -> Client:
        if self._client is None:
            raise ConnectionError("not connected to Contex")
        return self._client

    async def _tool(self, name: str, args: dict) -> dict:
        async with self._slots:
            result = await self._session_client().call_tool(name, args)
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
                result = await self._session_client().read_resource(uri)
        except Exception as e:
            if "Subscription expired" in str(e):
                raise SubscriptionGone(uri) from e
            raise
        raw = json.loads(result.contents[0].text)
        return {need: [Match(m["data_key"], m["data"], m.get("similarity")) for m in matches]
                for need, matches in raw.items()}

    async def query(self, project_id: str, query: str, top_k: int = 3,
                    threshold: float | None = None, max_age_seconds: int | None = None,
                    since: str | None = None) -> list[Match]:
        args = {"project_id": project_id, "query": query, "top_k": top_k}
        if threshold is not None:
            args["threshold"] = threshold
        if max_age_seconds is not None:
            args["max_age_seconds"] = max_age_seconds
        if since is not None:
            args["since"] = since
        found = await self._tool("contex_query", args)
        return [Match(m["data_key"], m["data"], m.get("similarity")) for m in found["matches"]]

    def listen(self, uri: str) -> AsyncIterator[None]:
        """Listen on one resource; see listen_many."""
        return self.listen_many([uri])

    async def listen_many(self, uris: list[str]) -> AsyncIterator[None]:
        """Yield once when the stream is acknowledged, then once per push on any of uris, until it closes.

        The first yield lets the caller catch up on anything published while it was not listening.
        """
        async with Client(self.url) as session:
            async with session.listen(resource_subscriptions=uris) as events:
                try:
                    yield
                    async for _ in events:
                        yield
                except GeneratorExit:
                    # Closing the generator must unwind the sessions normally, not as a task-group error.
                    return
