"""The command line: parse what the player types and run it."""

import asyncio
import sys
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from examples.rumor_mill.player import Player, Refusal
from examples.rumor_mill.players import Players
from examples.rumor_mill.suspicion import HEAT_LINES, Suspicion
from examples.rumor_mill.town import Town

HELP = (
    "Whisper by typing a name, a colon, and the rumor, e.g. Marta: the miller waters his flour\n"
    "Other commands: go <place>, listen, ask <name>: <topic>, villagers, inspect <name>, goals, trophies, usage, help, quit"
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
    if line in ("listen", "villagers", "goals", "trophies", "usage", "help", "quit"):
        return Command(line)
    if line.startswith("inspect ") and line[8:].strip():
        return Command("inspect", line[8:].strip())
    if line.startswith("go ") and line[3:].strip():
        return Command("go", text=line[3:].strip().lower())
    if line.startswith("ask "):
        name, sep, topic = line[4:].partition(":")
        return Command("ask", name.strip(), topic.strip()) if sep and name.strip() else Command("unknown")
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
        *([f"Grudge: {v.grudge}"] if v.grudge else []),
        f"Relationships: {relations or 'none'}",
    ]


async def _readline() -> str:
    return await asyncio.get_running_loop().run_in_executor(_STDIN, sys.stdin.readline)


async def until_over(session: Coroutine, player: Player) -> None:
    """Run the session until it returns or the player's game is over, cancelling whichever is left.

    An error raised by the session propagates.
    """
    running = asyncio.ensure_future(session)
    tasks = {running, asyncio.ensure_future(player.finished.wait())}
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if running.done() and not running.cancelled():
        running.result()


async def repl(
    player: Player, town: Town, feed, judge=None, usage=None, suspicion: Suspicion | None = None,
    players: Players | None = None,
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
        elif cmd.kind == "ask":
            found = player.find(cmd.name)
            problem = found if isinstance(found, str) else await player.ask(found.id, cmd.text)
            if problem and not isinstance(problem, Refusal):
                feed.say(problem)
        elif cmd.kind == "go":
            if problem := player.walk(cmd.text):
                feed.say(problem)
        elif cmd.kind == "listen":
            if problem := player.listen():
                feed.say(problem)
        elif cmd.kind == "villagers":
            for v in sorted(town.find(""), key=lambda v: v.name):
                feed.say(f"{v.name}, {v.occupation} ({town.place_of(v.id)})")
        elif cmd.kind == "inspect":
            for out in _inspect(town, cmd.name):
                feed.say(out)
        elif cmd.kind == "goals":
            progress = judge.progress if judge else {}
            for goal in player.goals:
                feed.say(f"{goal.text}: {int(progress.get(goal.id, 0.0) * 100)}%")
            if not player.goals:
                feed.say("No goals yet.")
            if judge:
                feed.say(f"World goal: {judge.world.text}: {int(progress.get(judge.world.id, 0.0) * 100)}%")
            if suspicion:
                feed.say(HEAT_LINES[suspicion.label(player.id)])
        elif cmd.kind == "trophies":
            case = players.cases.get(player.owner, []) if players else []
            for t in case:
                feed.say(f"Day {t.day}: {'; '.join(t.goals)}")
            if not case:
                feed.say("No trophies yet.")
        elif cmd.kind == "usage" and usage:
            feed.say(usage.summary())
        elif cmd.kind == "help" or line.strip():
            feed.say(HELP)
