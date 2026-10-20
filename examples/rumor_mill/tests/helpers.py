"""Test doubles and helpers shared by the Rumor Mill tests."""

import asyncio

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Villager
from examples.rumor_mill.player import Player
from examples.rumor_mill.suspicion import Suspicion
from examples.rumor_mill.town import Town

TIMEOUT = 2.0


class FakeMind:
    def __init__(self, villager):
        self.villager = villager
        self.queue: asyncio.Queue = asyncio.Queue()
        self.results: list[list[Match]] = []
        self.matches: list[Match] = []
        self.heard = []
        self.polls = 0

    async def open(self):
        pass

    async def hear(self, key, data):
        self.heard.append((key, data))

    async def poll(self):
        self.polls += 1
        fresh = self.results.pop(0) if self.results else [Match("k", {"id": "u-0", "text": "news"})]
        self.matches = fresh
        return fresh

    async def renew(self):
        pass

    async def pushes(self):
        while True:
            yield await self.queue.get()


def make(vid):
    return Villager(vid, vid.upper(), "baker", "calm", "none", ["bread"], {})


async def wait_for(predicate, timeout=TIMEOUT):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class FakeEngine:
    def __init__(self, suspicion=None):
        self.delivered = []
        self.suspicion = suspicion

    async def deliver(self, utterance, listener_ids):
        self.delivered.append((utterance, listener_ids))


def make_player(*names, shrug=0.05, cooldown=0.0):
    town, lines = Town(), []
    config = Config(shrug_seconds=shrug, cooldown_seconds=cooldown)
    engine = FakeEngine(Suspicion(config, town))
    lineage = Lineage()
    for i, name in enumerate(names, 1):
        town.add(Villager(f"v{i}", name, "baker", "calm", "none", [], {}), "tavern")
    feed = Feed(lineage, town, out=lines.append)
    player = Player(engine, town, lineage, Ids(), feed, config)
    return player, town, lines, engine, lineage
