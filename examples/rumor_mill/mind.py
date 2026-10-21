"""A villager's mind: one Contex project and one subscription per need."""

import asyncio
from contextlib import aclosing
from collections.abc import AsyncIterator
from dataclasses import replace

from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Bundle, ContexClient, Match, SubscriptionGone, new_matches
from examples.rumor_mill.models import Villager

REOPEN_PAUSE_SECONDS = 1


def mind_project(session: str, villager_id: str) -> str:
    return f"rumor-mill:{session}:mind:{villager_id}"


def said_project(session: str, villager_id: str) -> str:
    return f"rumor-mill:{session}:said:{villager_id}"


def group_project(session: str, gid: str) -> str:
    return f"rumor-mill:{session}:group:{gid}"


class Mind:
    def __init__(self, contex: ContexClient, villager: Villager, config: Config, baselines: Baselines):
        self.contex = contex
        self.villager = villager
        self.config = config
        self.baselines = baselines
        self.project = mind_project(config.session, villager.id)
        self.uris: list[str] = []
        self.seen: set[str] = set()
        self.matches: list[Match] = []
        self._epoch = 0

    async def open(self) -> None:
        """Create (or resume) one subscription per need, at that need's threshold."""
        self.uris = [await self._subscribe(need) for need in self.villager.needs]

    async def _subscribe(self, need: str) -> str:
        return await self.contex.subscribe(
            self.project, [need],
            max_age_seconds=self.config.memory_seconds,
            top_k=self.config.top_k, threshold=self.baselines.threshold(need),
        )

    async def hear(self, key: str, data: dict) -> None:
        await self.contex.publish(self.project, key, data)

    async def _read(self) -> Bundle:
        """Read every need's bundle into one, re-subscribing only the subscriptions that expired.

        Each match carries its best clearance across the needs whose bundle holds it.
        """
        bundles = await asyncio.gather(*(self.contex.read(uri) for uri in self.uris), return_exceptions=True)
        merged: Bundle = {}
        best: dict[str, float] = {}
        for i, bundle in enumerate(bundles):
            if isinstance(bundle, SubscriptionGone):
                self.uris[i] = await self._subscribe(self.villager.needs[i])
                self._epoch += 1
                bundle = await self.contex.read(self.uris[i])
            elif isinstance(bundle, BaseException):
                raise bundle
            threshold = self.baselines.threshold(self.villager.needs[i])
            for match in (m for ms in bundle.values() for m in ms if m.similarity is not None):
                clearance = match.similarity - threshold
                best[match.data_key] = max(clearance, best.get(match.data_key, clearance))
            merged.update(bundle)
        return {need: [replace(m, clearance=best.get(m.data_key)) for m in ms] for need, ms in merged.items()}

    async def poll(self) -> list[Match]:
        """Matches not in the previous poll; forgotten items count as new if they return."""
        bundle = await self._read()
        fresh = new_matches(self.seen, bundle)
        self.matches = new_matches(set(), bundle)
        self.seen = {m.data_key for matches in bundle.values() for m in matches}
        return fresh

    async def renew(self) -> None:
        """Keep the subscriptions alive without touching seen."""
        await self._read()

    async def pushes(self) -> AsyncIterator[None]:
        """Yield per push on any need and once per (re)connect, re-opening the stream when it closes or after a resubscribe.

        A mind with no needs never yields.
        """
        if not self.uris:
            await asyncio.Event().wait()
        while True:
            epoch = self._epoch
            async with aclosing(self.contex.listen_many(self.uris)) as stream:
                async for _ in stream:
                    yield
                    if epoch != self._epoch:
                        break
            await asyncio.sleep(REOPEN_PAUSE_SECONDS)
