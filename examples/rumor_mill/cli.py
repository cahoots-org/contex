"""The command line: parse what the player types and run it."""

import asyncio
import sys
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from examples.rumor_mill.game import Game
from examples.rumor_mill.player import Player, Refusal
from examples.rumor_mill.suspicion import HEAT_LINES, Suspicion
from examples.rumor_mill.town import Town

HELP = (
    "Whisper by typing a name, a colon, and the rumor, e.g. Marta: the miller waters his flour\n"
    "Other commands: villagers, inspect <name>, goals, usage, help, quit"
)


# Its own thread, so a read still blocked on stdin at exit does not hold up asyncio.run.
_STDIN = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stdin")


@dataclass(frozen=True)
class Command:
    kind: str
    name: str = ""
    text: str = ""


def parse_command(line: str) -> Command:
    """Turn one typed line into a Command."""
    line = line.strip()
    if line in ("villagers", "goals", "usage", "help", "quit"):
        return Command(line)
    if line.startswith("inspect ") and line[8:].strip():
        return Command("inspect", line[8:].strip())
    if line.startswith("whisper ") and ":" in line:
        line = line[8:]
    name, sep, text = line.partition(":")
    if sep and name.strip():
        return Command("whisper", name.strip(), text.strip())
    return Command("unknown")


def _inspect(town: Town, name: str) -> list[str]:
    found = town.find(name)
    if not found:
        return [f"No one in town is called {name}."]
    if len(found) > 1:
        return [f"Which {name}? " + ", ".join(v.name for v in found)]
    v = found[0]
    relations = ", ".join(f"{town.name_of(rid) or rid} ({kind})" for rid, kind in v.relationships.items())
    return [
        f"{v.name}, {v.occupation}",
        f"Personality: {v.personality}",
        f"Grudge: {v.grudge}",
        f"Relationships: {relations or 'none'}",
    ]


async def _readline() -> str:
    return await asyncio.get_running_loop().run_in_executor(_STDIN, sys.stdin.readline)


async def until_over(session: Coroutine, game: Game) -> None:
    """Run the session until it returns or the game is over, cancelling whichever is left.

    An error raised by the session propagates.
    """
    running = asyncio.ensure_future(session)
    tasks = {running, asyncio.ensure_future(game.finished.wait())}
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if running.done() and not running.cancelled():
        running.result()


async def repl(
    player: Player, town: Town, feed, goals=(), progress=None, usage=None, suspicion: Suspicion | None = None,
) -> None:
    """Read commands until quit or end of input; stdin is read off the event loop."""
    feed.say(HELP)
    while True:
        line = await _readline()
        if not line:
            return
        cmd = parse_command(line)
        if cmd.kind == "quit":
            return
        if cmd.kind == "whisper":
            problem = await player.whisper(cmd.name, cmd.text)
            if problem and not isinstance(problem, Refusal):
                feed.say(problem)
        elif cmd.kind == "villagers":
            for v in sorted(town.find(""), key=lambda v: v.name):
                feed.say(f"{v.name}, {v.occupation} ({town.place_of(v.id)})")
        elif cmd.kind == "inspect":
            for out in _inspect(town, cmd.name):
                feed.say(out)
        elif cmd.kind == "goals":
            for goal in goals:
                feed.say(f"{goal.text}: {int((progress or {}).get(goal.id, 0.0) * 100)}%")
            if not goals:
                feed.say("No goals yet.")
            if suspicion:
                feed.say(HEAT_LINES[suspicion.label()])
        elif cmd.kind == "usage" and usage:
            feed.say(usage.summary())
        elif cmd.kind == "help" or line.strip():
            feed.say(HELP)
