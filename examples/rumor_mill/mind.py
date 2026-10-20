"""A villager's mind: one Contex project and one subscription of their needs."""

import asyncio
from contextlib import aclosing
from collections.abc import AsyncIterator

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match, SubscriptionGone, new_matches
from examples.rumor_mill.models import Villager

REOPEN_PAUSE_SECONDS = 1


def mind_project(session: str, villager_id: str) -> str:
    return f"rumor-mill:{session}:mind:{villager_id}"


def group_project(session: str, gid: str) -> str:
    return f"rumor-mill:{session}:group:{gid}"


class Mind:
    def __init__(self, contex: ContexClient, villager: Villager, config: Config):
        self.contex = contex
        self.villager = villager
        self.config = config
        self.project = mind_project(config.session, villager.id)
        self.uri = ""
        self.seen: set[str] = set()
        self.matches: list[Match] = []
        self._epoch = 0

    async def open(self) -> None:
        """Create (or resume) the subscription on the villager's needs."""
        self.uri = await self.contex.subscribe(
            self.project, self.villager.needs,
            max_age_seconds=self.config.memory_seconds,
            top_k=self.config.top_k, threshold=self.config.threshold,
        )

    async def hear(self, key: str, data: dict) -> None:
        await self.contex.publish(self.project, key, data)

    async def _read(self):
        """Read the bundle, re-subscribing once if the subscription expired."""
        try:
            return await self.contex.read(self.uri)
        except SubscriptionGone:
            await self.open()
            self._epoch += 1
            return await self.contex.read(self.uri)

    async def poll(self) -> list[Match]:
        """Matches not in the previous poll; forgotten items count as new if they return."""
        bundle = await self._read()
        fresh = new_matches(self.seen, bundle)
        self.matches = new_matches(set(), bundle)
        self.seen = {m.data_key for matches in bundle.values() for m in matches}
        return fresh

    async def renew(self) -> None:
        """Keep the subscription alive without touching seen."""
        await self._read()

    async def pushes(self) -> AsyncIterator[None]:
        """Yield per push and once per (re)connect, re-opening the stream when it closes or after a resubscribe."""
        while True:
            epoch = self._epoch
            async with aclosing(self.contex.listen(self.uri)) as stream:
                async for _ in stream:
                    yield
                    if epoch != self._epoch:
                        break
            await asyncio.sleep(REOPEN_PAUSE_SECONDS)
