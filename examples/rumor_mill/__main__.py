"""Play the Rumor Mill: python -m examples.rumor_mill [--web]"""

import argparse
import asyncio
import logging
import os
import random
import sys

import anthropic
import typesafe_sdk
import uvicorn
from dotenv import load_dotenv

from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.cli import repl, until_over
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient
from examples.rumor_mill.engine import Engine
from examples.rumor_mill.facts import Facts
from examples.rumor_mill.feed import Feed, terminal
from examples.rumor_mill.game import Game
from examples.rumor_mill import goals
from examples.rumor_mill.believability import Believability
from examples.rumor_mill.goal_maker import GoalMaker
from examples.rumor_mill.judge import Judge
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.overhear import Overhear
from examples.rumor_mill.player import Player
from examples.rumor_mill.pool import load_pool
from examples.rumor_mill.players import LOCAL_OWNER, Players, new_player_id
from examples.rumor_mill.population import Replenisher
from examples.rumor_mill.routine import Routine
from examples.rumor_mill.rumors import Rumors
from examples.rumor_mill.town import GATE, PLACES, Town
from examples.rumor_mill.town_gen import SeedMismatch, load_or_generate
from examples.rumor_mill.usage import CountingJev, Usage
from examples.rumor_mill.web import create_app
from examples.rumor_mill.writer import Writer


async def play(web: bool) -> None:
    config = Config.from_env()
    rng = random.Random()
    usage = Usage(config)
    writer = Writer(anthropic.AsyncAnthropic(), config.haiku_model, usage)
    villagers = await load_or_generate(config.seed_path, writer, config.population, rng)
    town, lineage, ids = Town(), Lineage(), Ids()
    feed = Feed(lineage, town, out=None)
    game = Game(config.memory_moves, config.day_seconds)
    async with ContexClient(config.contex_url) as contex:
        jev = CountingJev(typesafe_sdk.AsyncTypeSafeClient(), usage)
        believability = Believability(jev)
        await goals.rebar_deck(believability)
        pool = await load_pool(config.seed_path + ".goals.json", writer, believability, villagers)  # noqa: F841
        baselines = Baselines(contex, config)
        facts = Facts()
        overhear = Overhear(contex, baselines, config, rng)
        routine = Routine(jev, config.seed_path + ".places.json", config)
        engine = Engine(
            config, town, contex, jev, writer, lineage, ids, feed, game=game, summary_source=lambda p: judge.percents(p.goals),
            baselines=baselines, rumors=Rumors(contex, baselines, config, overhear), overhear=overhear,
            routine=routine, facts=facts,
        )
        maker = GoalMaker(writer, believability, baselines, overhear, town, config, rng, ids)
        judge = Judge(
            contex, jev, town, config, feed, game, baselines=baselines, rng=rng, lineage=lineage, facts=facts,
            engine=engine, maker=maker,
        )
        players = Players(feed)
        engine.players = players

        def join(owner: str) -> Player:
            """Start a new run for the owner and deal it goals."""
            player = Player(engine, town, lineage, ids, feed, config, writer, game, player_id=new_player_id(), owner=owner)
            players.add(player)
            judge.deal(player)
            players.deliver_pending(player)
            return player

        replenisher = asyncio.create_task(Replenisher(engine, town, writer, config, rng).run())
        feed.on_world(judge.world, judge.counts_of(judge.world))
        if not web:
            player = join(LOCAL_OWNER)
            show = terminal(print)
            for event in feed.replay(player.id):
                show(event)
            feed.sinks.append(feed.view(player.id, show))
        places = [p for p in PLACES if p != GATE]
        judging = None
        try:
            await baselines.load([need for v in villagers for need in v.needs])
            await baselines.load([g.evidence[0] for g in goals.DECK if g.evidence])
            await overhear.publish(list(goals.DECK))
            judging = asyncio.create_task(judge.run(players))
            await routine.load(villagers)
            await asyncio.gather(*(
                engine.add_villager(v, routine.place(v.id, "morning") or rng.choice(places)) for v in villagers
            ))
            await engine.run()
            if web:
                app = create_app(
                    players=players, join=join, town=town, feed=feed, judge=judge, usage=usage, port=config.web_port,
                    suspicion=engine.suspicion, game=game, rumors=engine.rumors, hints=engine.hints,
                )
                server = uvicorn.Server(uvicorn.Config(
                    app, host="127.0.0.1", port=config.web_port, log_level="warning", timeout_graceful_shutdown=1,
                ))
                print(f"Rumor Mill is running at http://127.0.0.1:{config.web_port} (Ctrl-C to stop)")
                await server.serve()
            else:
                await until_over(repl(player, town, feed, judge, usage, engine.suspicion, players), player)
        finally:
            print(usage.summary())
            tasks = [t for t in (replenisher, judging) if t is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for run in players:
                run.close()
            await engine.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m examples.rumor_mill")
    parser.add_argument("--web", action="store_true", help="play in the browser instead of the terminal")
    args = parser.parse_args()
    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    sys.stdout.reconfigure(line_buffering=True)
    try:
        asyncio.run(play(args.web))
    except SeedMismatch as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        pass
    # A thread blocked on stdin would stall interpreter exit.
    os._exit(0)


main()
