"""The accusations and claims a village's goals are filled from, written once and kept beside the seed."""

import json
import logging
import os
import re
import tempfile
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from examples.rumor_mill.models import Villager

log = logging.getLogger(__name__)

MAX_LENGTH = 80
SECOND_PERSON = re.compile(r"\byou(rs?|rself)?\b", re.IGNORECASE)

DEFAULT_ACCUSATIONS: tuple[str, ...] = (
    "is stealing from the tithe box",
    "waters down the ale",
    "cheats at dice",
    "pockets the market fees",
    "sells spoiled meat",
    "keeps false weights",
    "poisons the neighbours' dogs",
    "sneaks out at night to meet a stranger",
    "lies about where the money goes",
    "reads other people's letters",
    "is secretly in debt to a moneylender",
    "moves the boundary stones at night",
    "takes bribes from travellers",
    "hoards grain while others go hungry",
    "spreads lies about the mayor",
    "is not who they claim to be",
    "sold a stolen horse",
    "spies on the village for outsiders",
    "has been skimming the harvest share",
    "poisoned the well last winter",
)

DEFAULT_CLAIMS: tuple[str, ...] = (
    "the mayor's cat is a spy",
    "the well is cursed",
    "the miller cheats customers by watering down flour",
    "the tavern is haunted by a ghost",
    "treasure is buried under the chapel",
    "the coming harvest will fail",
    "a dangerous stranger is hiding in the village",
    "the mayor will impose a new market tax",
    "the strange moon is an omen of disaster",
    "a plague is coming to the village",
    "there is a gold mine beyond the gate",
    "the mayor is stealing from the village",
    "a dragon lives in the hills",
    "a secret midnight festival is being planned",
)


@dataclass(frozen=True)
class Entry:
    text: str
    band: int


@dataclass(frozen=True)
class Pool:
    accusations: tuple[Entry, ...]
    claims: tuple[Entry, ...]


def clean(texts: Iterable[str], names: Collection[str]) -> list[str]:
    """The texts that fit the pool: short, third person, naming no villager, each once."""
    named = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b", re.IGNORECASE) if names else None
    kept: list[str] = []
    for text in texts:
        text = text.strip()
        if not text or len(text) > MAX_LENGTH or text in kept or SECOND_PERSON.search(text):
            continue
        if named and named.search(text):
            continue
        kept.append(text)
    return kept


def _read(path: str) -> dict[str, list]:
    try:
        with open(path) as f:
            data = json.load(f)
        return {k: data[k] for k in ("accusations", "claims") if isinstance(data.get(k), list)}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def _write(path: str, pool: Pool) -> None:
    """Save the pool beside the seed; a failure is logged and the game goes on."""
    data = {k: [{"text": e.text, "band": e.band} for e in getattr(pool, k)] for k in ("accusations", "claims")}
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        log.warning("saving the goal pool failed")
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def _parts(item) -> tuple[str, int | None]:
    """A stored entry's text and band, the band None when it has none."""
    if isinstance(item, dict):
        band = item.get("band")
        return str(item.get("text", "")), band if isinstance(band, int) and 0 <= band <= 3 else None
    return str(item), None


async def _entries(items: list, names: Collection[str], believability, prefix: str) -> tuple[Entry, ...]:
    bands = dict(_parts(item) for item in items)
    texts = clean([_parts(item)[0] for item in items], names)
    return tuple([Entry(t, bands[t] if bands[t] is not None else await believability.band(prefix + t)) for t in texts])


async def load_pool(path: str, writer, believability, villagers: list[Villager]) -> Pool:
    """The village's pool from path, written by Haiku or taken from the defaults when there is none."""
    raw = _read(path)
    if not raw:
        try:
            raw = await writer.goal_pool(villagers)
        except Exception:
            log.warning("could not write a goal pool", exc_info=True)
            raw = {}
        raw = {k: raw.get(k) if isinstance(raw.get(k), list) else [] for k in ("accusations", "claims")}
        if not any(raw.values()):
            raw = {"accusations": list(DEFAULT_ACCUSATIONS), "claims": list(DEFAULT_CLAIMS)}
    names = [v.name.split()[0] for v in villagers]
    pool = Pool(
        await _entries(raw.get("accusations", []), names, believability, "someone "),
        await _entries(raw.get("claims", []), names, believability, ""),
    )
    _write(path, pool)
    return pool
