"""Play the Rumor Mill: python -m examples.rumor_mill"""

import asyncio
import logging
import os
import random
import sys

import anthropic
import typesafe_sdk

from examples.rumor_mill.cli import repl
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient
from examples.rumor_mill.engine import Engine
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.player import Player
from examples.rumor_mill.town import GATE, PLACES, Town
from examples.rumor_mill.town_gen import load_or_generate
from examples.rumor_mill.writer import Writer


async def play() -> None:
    config = Config.from_env()
    rng = random.Random()
    writer = Writer(anthropic.AsyncAnthropic(), config.haiku_model)
    villagers = await load_or_generate(config.seed_path, writer, config.population, rng)
    town, lineage, ids = Town(), Lineage(), Ids()
    feed = Feed(lineage, town)
    async with ContexClient(config.contex_url) as contex:
        engine = Engine(config, town, contex, typesafe_sdk.AsyncTypeSafeClient(), writer, lineage, ids, feed)
        player = Player(engine, town, lineage, ids, feed, config)
        engine.on_heard = player.note_heard
        places = [p for p in PLACES if p != GATE]
        try:
            await asyncio.gather(*(engine.add_villager(v, rng.choice(places)) for v in villagers))
            await engine.run()
            await repl(player, town, feed)
        finally:
            player.close()
            await engine.stop()


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    sys.stdout.reconfigure(line_buffering=True)
    try:
        asyncio.run(play())
    except KeyboardInterrupt:
        # A thread blocked on stdin would stall interpreter exit.
        os._exit(0)


main()
