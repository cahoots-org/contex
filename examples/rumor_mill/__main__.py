"""Play the Rumor Mill: python -m examples.rumor_mill"""

import asyncio
import logging
import os
import random
import sys

import anthropic
import typesafe_sdk
from dotenv import load_dotenv

from examples.rumor_mill.cli import repl
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient
from examples.rumor_mill.engine import Engine
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.goals import deal
from examples.rumor_mill.judge import Judge
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.player import Player
from examples.rumor_mill.population import Replenisher
from examples.rumor_mill.town import GATE, PLACES, Town
from examples.rumor_mill.town_gen import SeedMismatch, load_or_generate
from examples.rumor_mill.writer import Writer


async def play() -> None:
    config = Config.from_env()
    rng = random.Random()
    writer = Writer(anthropic.AsyncAnthropic(), config.haiku_model)
    villagers = await load_or_generate(config.seed_path, writer, config.population, rng)
    town, lineage, ids = Town(), Lineage(), Ids()
    feed = Feed(lineage, town)
    async with ContexClient(config.contex_url) as contex:
        jev = typesafe_sdk.AsyncTypeSafeClient()
        engine = Engine(config, town, contex, jev, writer, lineage, ids, feed)
        player = Player(engine, town, lineage, ids, feed, config)
        engine.on_heard = player.note_heard
        replenisher = asyncio.create_task(Replenisher(engine, town, writer, config, rng).run())
        goals = deal(rng)
        judge = Judge(contex, jev, town, config, feed)
        for goal in goals:
            feed.say(f"Goal: {goal.text}")
        judging = asyncio.create_task(judge.run(goals))
        places = [p for p in PLACES if p != GATE]
        try:
            await asyncio.gather(*(engine.add_villager(v, rng.choice(places)) for v in villagers))
            await engine.run()
            await repl(player, town, feed, goals, judge.progress)
        finally:
            replenisher.cancel()
            judging.cancel()
            await asyncio.gather(replenisher, judging, return_exceptions=True)
            player.close()
            await engine.stop()


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    sys.stdout.reconfigure(line_buffering=True)
    try:
        asyncio.run(play())
    except SeedMismatch as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        # A thread blocked on stdin would stall interpreter exit.
        os._exit(0)


main()
