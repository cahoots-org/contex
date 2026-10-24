"""Saving the running game to one JSON file, and restoring it on start."""

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable

from examples.whispers.town_gen import WORLDS_DIR

log = logging.getLogger(__name__)

STATE_VERSION = 1
# Every id the game's Ids counters hand out, as prefix and number.
ID = re.compile(r"\b(world|goal|u|w)-(\d+)\b")


def state_path(session: str, data_dir: str = "") -> str:
    """Where the session's game is saved, beside its village."""
    return os.path.join(data_dir, WORLDS_DIR, f"{session}.state.json")


def components(engine) -> dict:
    """The game's stateful parts by name, in the order they are restored."""
    return {
        "town": engine.town, "ids": engine.ids, "lineage": engine.lineage, "facts": engine.facts, "feed": engine.feed,
        "game": engine.game, "usage": engine.players.usage, "suspicion": engine.suspicion, "rapport": engine.rapport,
        "pressure": engine.pressure, "engine": engine, "rumors": engine.rumors, "players": engine.players,
        "judge": engine.judge, "hints": engine.hints,
    }


def save_game(path: str, components: dict, session: str) -> None:
    """Write every component's state to path atomically: a temp file beside it, then a rename."""
    state = {"version": STATE_VERSION, "saved_at": time.time(), "session": session,
             **{name: part.to_state() for name, part in components.items()}}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp = f"{path}.tmp"
    with open(temp, "w") as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def load_game(path: str, session: str) -> dict | None:
    """The saved game at path, or None, logged, when it is missing, unreadable, or for another version or session."""
    try:
        with open(path) as f:
            state = json.load(f)
    except FileNotFoundError:
        log.info("no saved game at %s; starting fresh", path)
        return None
    except (OSError, ValueError):
        log.warning("the saved game at %s is unreadable; starting fresh", path, exc_info=True)
        return None
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION or state.get("session") != session:
        log.warning("the saved game at %s is for another version or session; starting fresh", path)
        return None
    return state


def restore_game(state: dict, components: dict, make: Callable) -> bool:
    """Restore every component from state and resume the id counters; make(id, owner) builds a wired run.

    A state that fails part way is logged and the fresh state put back, and False returned.
    """
    fresh = {name: part.to_state() for name, part in components.items()}
    try:
        _restore(state, components, make)
    except Exception:
        log.exception("the saved game could not be restored; starting fresh")
        _restore(fresh, components, make)
        return False
    return True


def _restore(state: dict, components: dict, make: Callable) -> None:
    for name, part in components.items():
        if name == "players":
            part.restore(state[name], make)
        else:
            part.restore(state[name])
    for prefix, n in ID.findall(json.dumps(state)):
        components["ids"].ensure_past(prefix, int(n))
    feed = state["feed"]
    components["feed"].ensure_past(max([e["id"] for e in feed["recent"]] + list(feed["closed"].values()), default=0))


def save_quietly(path: str, components: dict, session: str) -> None:
    """Save the game; a failure is logged, not raised."""
    try:
        save_game(path, components, session)
    except Exception:
        log.exception("saving the game to %s failed", path)


async def save_every(seconds: float, save: Callable[[], None]) -> None:
    """Save once every seconds until cancelled."""
    while True:
        await asyncio.sleep(seconds)
        save()


def stop(save: Callable[[], None], task: asyncio.Task) -> None:
    """On a stop signal: save, then cancel the game's main task so it shuts down."""
    save()
    task.cancel()
