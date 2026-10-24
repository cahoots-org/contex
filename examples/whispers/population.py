"""Keeps the village at its target size by sending newcomers to the gate."""

import asyncio
import logging
import random

from examples.whispers.config import Config
from examples.whispers.models import Utterance
from examples.whispers.town import GATE, START_PLACE, Town
from examples.whispers.town_gen import generate_newcomer
from examples.whispers.writer import Writer

log = logging.getLogger(__name__)

CRIER = "The town crier"


class Replenisher:
    def __init__(self, engine, town: Town, writer: Writer, config: Config, rng: random.Random):
        self.engine = engine
        self.town = town
        self.writer = writer
        self.config = config
        self.rng = rng

    async def run(self) -> None:
        """After each departure, wait, then top the village back up to the target."""
        while True:
            await self.engine.departures.get()
            await asyncio.sleep(self.config.arrival_delay_seconds)
            while not self.engine.departures.empty():
                self.engine.departures.get_nowait()
            await self._top_up()

    async def _top_up(self) -> None:
        while self.town.alive_count < self.config.population and self.engine.players.any_playing():
            try:
                await self._admit()
            except Exception:
                log.exception("could not add a newcomer")
                return

    async def _admit(self) -> None:
        newcomer = await generate_newcomer(
            self.writer, self.town.villagers, self.rng, departed=self.town.departed,
        )
        await self.engine.baselines.load(newcomer.needs)
        if self.engine.routine:
            await self.engine.routine.add(newcomer)
        await self.engine.add_villager(newcomer, GATE)
        self.engine.feed.on_arrival(newcomer.id, newcomer.name, newcomer.occupation)
        utt = Utterance(
            self.engine.ids.next("u"), "town", CRIER, GATE,
            f"{newcomer.name}, a {newcomer.occupation}, has arrived at the gate.", None, "announced",
        )
        self.engine.lineage.record(utt.id, None)
        await self.engine.deliver(utt, [v.id for v in self.town.present(START_PLACE)])
