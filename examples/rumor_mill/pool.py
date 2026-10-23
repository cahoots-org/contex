"""The accusations and claims a village's goals are filled from, written once and kept beside the seed."""

import asyncio
import json
import logging
import os
import re
import tempfile
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from examples.rumor_mill.believability import MIDDLING
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


def tidy(text: str) -> str:
    """The text as it reads mid-sentence: trimmed, no closing stop, lower-case first letter unless an acronym."""
    text = text.strip().rstrip(".!")
    return text[0].lower() + text[1:] if text[:2].istitle() else text


def clean(texts: Iterable[str], names: Collection[str]) -> list[str]:
    """The texts that fit the pool: short, third person, naming no villager, each once."""
    named = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b", re.IGNORECASE) if names else None
    kept: list[str] = []
    for text in texts:
        text = tidy(text)
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
    """A stored entry's stripped text and band, the band None when it has none."""
    if isinstance(item, dict):
        band = item.get("band")
        return tidy(str(item.get("text", ""))), band if isinstance(band, int) and 0 <= band <= 3 else None
    return tidy(str(item)), None


async def _entries(items: list, defaults: tuple[str, ...], names: Collection[str], believability, prefix: str):
    """The cleaned, scored entries, and whether any is provisional: a default, or a band Jev could not give."""
    bands = dict(_parts(item) for item in items)
    texts = clean(bands, names)
    fell_back = not texts
    if fell_back:
        bands, texts = {}, clean(defaults, names)
    missing = [t for t in texts if bands.get(t) is None]
    bands.update(zip(missing, await asyncio.gather(*(believability.band(prefix + t) for t in missing))))
    entries = tuple(Entry(t, MIDDLING if bands[t] is None else bands[t]) for t in texts)
    return entries, fell_back or any(bands[t] is None for t in missing)


async def load_pool(path: str, writer, believability, villagers: list[Villager]) -> Pool:
    """The village's pool from path or Haiku, with defaults for a list that has nothing usable.

    Only a pool made wholly of real, scored entries is saved, so a fallback or an unscored band is retried
    on the next launch.
    """
    raw = _read(path)
    if not raw:
        try:
            raw = await writer.goal_pool(villagers)
        except Exception:
            log.warning("could not write a goal pool", exc_info=True)
            raw = {}
    names = [v.name.split()[0] for v in villagers]
    accusations, fell_a = await _entries(_listed(raw, "accusations"), DEFAULT_ACCUSATIONS, names, believability, "someone ")
    claims, fell_c = await _entries(_listed(raw, "claims"), DEFAULT_CLAIMS, names, believability, "")
    pool = Pool(accusations, claims)
    if not (fell_a or fell_c):
        _write(path, pool)
    return pool


def _listed(raw: dict, key: str) -> list:
    return raw.get(key) if isinstance(raw.get(key), list) else []
