"""The command line: parse what the player types and run it."""

import asyncio
import sys
from dataclasses import dataclass

from examples.rumor_mill.player import Player
from examples.rumor_mill.town import Town

HELP = "Commands: <name>: <text> | villagers | inspect <name> | goals | quit"


@dataclass(frozen=True)
class Command:
    kind: str
    name: str = ""
    text: str = ""


def parse_command(line: str) -> Command:
    """Turn one typed line into a Command."""
    line = line.strip()
    if line in ("villagers", "goals", "quit"):
        return Command(line)
    if line.startswith("inspect ") and line[8:].strip():
        return Command("inspect", line[8:].strip())
    if line.startswith("whisper ") and ":" in line:
        line = line[8:]
    name, sep, text = line.partition(":")
    if sep and name.strip():
        return Command("whisper", name.strip(), text.strip())
    return Command("unknown")


def _name(town: Town, vid: str) -> str:
    try:
        return town.get(vid).name
    except KeyError:
        return next((v.name for v in town.departed if v.id == vid), vid)


def _inspect(town: Town, name: str) -> list[str]:
    found = town.find(name)
    if not found:
        return [f"No one in town is called {name}."]
    if len(found) > 1:
        return [f"Which {name}? " + ", ".join(v.name for v in found)]
    v = found[0]
    relations = ", ".join(f"{_name(town, rid)} ({kind})" for rid, kind in v.relationships.items())
    return [
        f"{v.name}, {v.occupation}",
        f"Personality: {v.personality}",
        f"Grudge: {v.grudge}",
        f"Relationships: {relations or 'none'}",
    ]


async def repl(player: Player, town: Town, feed, goals=(), progress=None) -> None:
    """Read commands until quit or end of input; stdin is read off the event loop."""
    feed.say(HELP)
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return
        cmd = parse_command(line)
        if cmd.kind == "quit":
            return
        if cmd.kind == "whisper":
            await player.whisper(cmd.name, cmd.text)
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
        elif line.strip():
            feed.say(HELP)
