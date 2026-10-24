"""Play Whispers: python -m examples.whispers [--web]"""

import argparse
import asyncio
import logging
import os
import random
import signal
import sys

import anthropic
import typesafe_sdk
import uvicorn
from dotenv import load_dotenv

from examples.whispers.calibration import Baselines
from examples.whispers.cli import repl, until_over
from examples.whispers.config import Config
from examples.whispers.contex_client import ContexClient
from examples.whispers.engine import Engine
from examples.whispers.facts import Facts
from examples.whispers.feed import Feed, terminal
from examples.whispers.game import Game
from examples.whispers.believability import Believability
from examples.whispers.goal_maker import GoalMaker
from examples.whispers.judge import Judge
from examples.whispers.lineage import Ids, Lineage
from examples.whispers.overhear import Overhear
from examples.whispers.persist import components, resume_game, save_every, save_quietly, set_aside, state_path, stop
from examples.whispers.player import Player
from examples.whispers.pool import load_pool
from examples.whispers.players import LOCAL_OWNER, Players, new_player_id
from examples.whispers.population import Replenisher
from examples.whispers.routine import Routine
from examples.whispers.rumors import Rumors
from examples.whispers.town import GATE, PLACES, Town
from examples.whispers.town_gen import SeedMismatch, load_or_generate, world_path
from examples.whispers.usage import CountingJev, Usage
from examples.whispers.web import create_app
from examples.whispers.writer import Writer


async def play(web: bool) -> None:
    config = Config.from_env()
    rng = random.Random()
    usage = Usage(config)
    writer = Writer(anthropic.AsyncAnthropic(), config.haiku_model, usage)
    seed = config.seed_path or world_path(config.session, config.data_dir)
    kept = os.path.exists(seed)
    villagers = await load_or_generate(seed, writer, config.population, rng)
    if not config.seed_path:
        if kept:
            print(f"Kept the village saved as {seed}.")
        else:
            print(f"A new village, saved as {seed}. Set WHISPERS_SEED_PATH={seed} to play it again.")
    town, lineage, ids = Town(), Lineage(), Ids()
    feed = Feed(lineage, town, out=None)
    game = Game(config.memory_moves, config.day_seconds)
    async with ContexClient(config.contex_url) as contex:
        jev = CountingJev(typesafe_sdk.AsyncTypeSafeClient(), usage)
        believability = Believability(jev)
        pool = await load_pool(seed + ".goals.json", writer, believability, villagers)
        baselines = Baselines(contex, config)
        facts = Facts()
        overhear = Overhear(contex, baselines, config, rng)
        routine = Routine(jev, seed + ".places.json", config)
        engine = Engine(
            config, town, contex, jev, writer, lineage, ids, feed, game=game, summary_source=lambda p: judge.percents(p.goals),
            baselines=baselines, rumors=Rumors(contex, baselines, config, overhear), overhear=overhear,
            routine=routine, facts=facts,
        )
        maker = GoalMaker(jev, pool, town, routine, overhear, baselines, config, rng, ids)
        judge = Judge(
            contex, jev, town, config, feed, game, baselines=baselines, lineage=lineage, facts=facts,
            engine=engine, maker=maker,
        )
        players = Players(feed, usage)
        engine.players = players
        engine.judge = judge

        def make(player_id: str, owner: str) -> Player:
            return Player(engine, town, lineage, ids, feed, config, writer, game, player_id=player_id, owner=owner)

        def join(owner: str) -> Player:
            """Start a new run for the owner and deal it goals, dropping stale runs that never played."""
            player = make(new_player_id(), owner)
            players.add(player)
            for gone in players.prune():
                maker.overhear.drop([g.id for g in gone.goals])
                gone.close()
            judge.deal(player)
            players.deliver_pending(player)
            return player

        parts = components(engine)
        saves = state_path(config.session, config.data_dir)
        # Only a fixed session is started again, so only its game is saved.
        fixed = "WHISPERS_SESSION" in os.environ
        if fixed and not kept:
            set_aside(saves)
        if fixed and resume_game(saves, config.session, parts, make):
            maker.ready_instead([g for p in players if not p.over() for g in p.goals] + [judge.world])
            villagers = town.villagers
        else:
            feed.on_world(judge.world, judge.counts_of(judge.world))
        replenisher = asyncio.create_task(Replenisher(engine, town, writer, config, rng).run())
        places = [p for p in PLACES if p != GATE]
        judging = saving = None
        try:
            await baselines.load([need for v in villagers for need in v.needs])
            await maker.prepare()
            judging = asyncio.create_task(judge.run(players))
            await routine.load(villagers)
            await asyncio.gather(*(
                engine.add_villager(
                    v, town.place_of(v.id) if v.id in town else routine.place(v.id, "morning") or rng.choice(places),
                ) for v in villagers
            ))

            def save() -> None:
                save_quietly(saves, parts, config.session)

            if fixed:
                saving = asyncio.create_task(save_every(config.save_every_seconds, save))
                loop, main_task = asyncio.get_running_loop(), asyncio.current_task()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, stop, save, main_task)
            await engine.run()
            if web:
                host, port = config.address()
                app = create_app(
                    players=players, join=join, town=town, feed=feed, judge=judge, usage=usage, port=port,
                    suspicion=engine.suspicion, game=game, rumors=engine.rumors, hints=engine.hints, host=host,
                )
                server = uvicorn.Server(uvicorn.Config(
                    app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=1,
                    # Behind a TLS proxy such as Railway's, the forwarded scheme says the page was served over https.
                    proxy_headers=True, forwarded_allow_ips="*",
                ))
                print(f"Whispers is running at http://{host}:{port} (Ctrl-C to stop)")
                await server.serve()
            else:
                player = join(LOCAL_OWNER)
                # The terminal stays open for as long as the game runs.
                player.connected()
                show = terminal(print)
                for event in feed.replay(player.id):
                    show(event)
                feed.sinks.append(feed.view(player.id, show))
                await until_over(repl(player, town, feed, judge, usage, engine.suspicion, players), player)
        finally:
            print(usage.summary())
            tasks = [t for t in (replenisher, judging, saving) if t is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for run in players:
                run.close()
            await engine.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m examples.whispers")
    parser.add_argument("--web", action="store_true", help="play in the browser instead of the terminal")
    args = parser.parse_args()
    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("examples.whispers.engine").setLevel(logging.INFO)
    sys.stdout.reconfigure(line_buffering=True)
    try:
        asyncio.run(play(args.web))
    except SeedMismatch as e:
        sys.exit(str(e))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    # A thread blocked on stdin would stall interpreter exit.
    os._exit(0)


main()
