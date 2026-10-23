"""Generate the village: Haiku writes the people, code wires who knows whom."""

import json
import random
import re
from dataclasses import asdict

from examples.rumor_mill.models import Villager
from examples.rumor_mill.writer import Writer, extract_json

BATCH = 10
MAX_FAILED_BATCHES = 5
REQUIRED = ("name", "occupation", "personality", "grudge", "needs")
MAX_RELATIONSHIPS = 4
REVERSE = {
    "spouse": "spouse", "rival": "rival", "friend": "friend",
    "debtor": "creditor", "creditor": "debtor",
}
# Villagers the default goal pool talks about, asked for until the village has them.
ROLES = ("mayor", "miller")
# Weighted draw, so friends are commoner than spouses.
KINDS = ["friend", "friend", "rival", "debtor", "creditor", "spouse"]
# The share of villagers who are sour and hold a grudge.
SOUR_SHARE = 0.2

SYSTEM = (
    "You invent villagers for a small medieval village. Reply with only JSON, no prose."
)
PROMPT = (
    "Invent {n} distinct villagers as a top-level JSON array of objects, not wrapped in "
    "another object. Each item has: name (a full name), occupation, personality (one "
    "short phrase), grudge (a string, empty for most villagers), and needs: a JSON array of 3 to 5 strings, each one sentence in the villager's own voice "
    "about news they would prick up their ears at: one about their trade, one about a "
    "person they love or resent, one about their standing or money, and one about an odd "
    "fascination or fear of their own (omens, ghosts, the stars, the church, strangers, "
    "curses, the lord's taxes, or anything else). Make needs particular to this person and "
    "different from the other villagers', never generic topics like news or gossip. "
    "{temper}{roles}{taken}"
)
GOOD = (
    "good-natured, with a temperament such as warm, curious, cheerful, timid, nosy, pious, "
    'absent-minded, or kindly, and an empty grudge ""'
)
SOUR = (
    "sour, with a grudge: one sentence about someone or something they resent. Sour villagers "
    "are the village's skeptics, quick to doubt, deny, or mock the rumors they hear, and their "
    "personality shows it"
)
MIX = f"Most villagers are {GOOD}; vary their temperaments. Roughly one villager in five is {SOUR}. "


BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s*")


def _split_needs(text: str) -> list[str]:
    """Split a needs string on newlines and semicolons, else on sentence ends."""
    parts = re.split(r"[\n;]+", text)
    if len(parts) < 2:
        parts = re.split(r"(?<=[.!?])\s+", text)
    return [p for p in (BULLET.sub("", part).strip() for part in parts) if p]


def _prepare(raw):
    """Turn a needs string into a list of needs; anything else passes through."""
    if isinstance(raw, dict) and isinstance(raw.get("needs"), str):
        raw = {**raw, "needs": _split_needs(raw["needs"])}
    return raw


def _unwrap(parsed):
    """The villager list from a bare array or an object holding a single list."""
    if isinstance(parsed, dict):
        lists = [v for v in parsed.values() if isinstance(v, list)]
        return lists[0] if len(lists) == 1 else None
    return parsed if isinstance(parsed, list) else None


def _valid(raw) -> bool:
    """True for a dict with every required key and 3 or more string needs."""
    return (
        isinstance(raw, dict)
        and all(k in raw for k in REQUIRED)
        and isinstance(raw["needs"], list)
        and len(raw["needs"]) >= 3
        and all(isinstance(n, str) for n in raw["needs"])
    )


def _missing_roles(villagers: list[Villager]) -> str:
    """A prompt line asking for the ROLES no villager has yet, or nothing."""
    missing = [r for r in ROLES if not any(r in v.occupation.lower() for v in villagers)]
    return f"Include the village {' and the '.join(missing)} among them. " if missing else ""


def _villager(vid: str, raw: dict) -> Villager:
    return Villager(
        id=vid, name=raw["name"], occupation=raw["occupation"],
        personality=raw["personality"], grudge=raw["grudge"] or "",
        needs=list(raw["needs"])[:5], relationships={},
    )


# How many times to ask for a newcomer whose first name is already taken.
NEWCOMER_TRIES = 3


def _first(name: str) -> str:
    return name.split()[0].lower() if name.split() else ""


def _first_names_taken(villagers: list[Villager]) -> str:
    """The prompt line that keeps every first name in the village unique."""
    names = ", ".join(sorted({v.name.split()[0] for v in villagers if v.name.split()}))
    return f"Give each villager a first name no one else in the village has; these are taken: {names}."


async def generate_villagers(writer: Writer, count: int, rng: random.Random) -> list[Villager]:
    """Ask Haiku for villagers in batches of 10, with ids v001... and no relationships.

    Gives up after MAX_FAILED_BATCHES replies in a row that add no villager.
    """
    villagers: list[Villager] = []
    failed = 0
    while len(villagers) < count and failed < MAX_FAILED_BATCHES:
        failed += 1
        n = min(BATCH, count - len(villagers))
        taken = ""
        if villagers:
            jobs = ", ".join(v.occupation for v in villagers[-30:])
            taken = f"Do not reuse these occupations: {jobs}. {_first_names_taken(villagers)}"
        text = await writer.complete(SYSTEM, PROMPT.format(n=n, temper=MIX, roles=_missing_roles(villagers), taken=taken), max_tokens=4000)
        try:
            parsed = extract_json(text)
        except ValueError:
            continue
        items = _unwrap(parsed)
        if items is None:
            continue
        used = {_first(v.name) for v in villagers}
        for raw in [r for r in map(_prepare, items) if _valid(r)]:
            if len(villagers) >= count or _first(raw["name"]) in used:
                continue
            used.add(_first(raw["name"]))
            villagers.append(_villager(f"v{len(villagers) + 1:03d}", raw))
            failed = 0
    if len(villagers) < count:
        raise ValueError(
            f"got {len(villagers)} of {count} villagers after {MAX_FAILED_BATCHES} failed batches"
        )
    return villagers


def _link(a: Villager, b: Villager, rng: random.Random) -> None:
    """Add a reciprocal relationship; a spouse only if neither already has one."""
    kinds = KINDS
    if "spouse" in a.relationships.values() or "spouse" in b.relationships.values():
        kinds = [k for k in KINDS if k != "spouse"]
    kind = rng.choice(kinds)
    a.relationships[b.id] = kind
    b.relationships[a.id] = REVERSE[kind]


def _partner(v: Villager, villagers: list[Villager], rng: random.Random) -> Villager | None:
    """A random not-yet-related villager with room, preferring the least connected."""
    open_ = [
        o for o in villagers
        if o.id != v.id and o.id not in v.relationships
        and len(o.relationships) < MAX_RELATIONSHIPS
    ]
    if not open_:
        return None
    fewest = min(len(o.relationships) for o in open_)
    return rng.choice([o for o in open_ if len(o.relationships) == fewest])


def wire_relationships(villagers: list[Villager], rng: random.Random) -> None:
    """Give each villager 2-4 reciprocal relationships.

    Fewer than 2 only when impossible: fewer than 3 villagers, or every other
    villager is already at the cap of 4 or related to them.
    """
    while True:
        lonely = [v for v in villagers if len(v.relationships) < 2]
        stuck = True
        for v in sorted(lonely, key=lambda v: len(v.relationships)):
            other = _partner(v, villagers, rng)
            if other and len(v.relationships) < 2:
                _link(v, other, rng)
                stuck = False
        if stuck:
            break
    for v in rng.sample(villagers, len(villagers)):
        other = _partner(v, villagers, rng)
        if len(v.relationships) < 3 and other and rng.random() < 0.5:
            _link(v, other, rng)


class SeedMismatch(ValueError):
    """The seed file holds a different number of villagers than the configured population."""


async def load_or_generate(
    path: str, writer: Writer, count: int, rng: random.Random,
) -> list[Villager]:
    """Load the seed file if present, else generate, wire, and write it."""
    try:
        with open(path) as f:
            villagers = [Villager(**raw) for raw in json.load(f)]
    except FileNotFoundError:
        pass
    else:
        if len(villagers) != count:
            raise SeedMismatch(
                f"{path} has {len(villagers)} villagers but population is {count}; "
                f"delete it or set RUMOR_MILL_POPULATION={len(villagers)}"
            )
        return villagers
    villagers = await generate_villagers(writer, count, rng)
    wire_relationships(villagers, rng)
    with open(path, "w") as f:
        json.dump([asdict(v) for v in villagers], f, indent=2)
    return villagers


async def generate_newcomer(
    writer: Writer, existing: list[Villager], rng: random.Random,
    departed: list[Villager] = (),
) -> Villager:
    """A new villager related to one or two living ones; departed ones only reserve their id and name."""
    everyone = [*departed, *existing]
    taken = _first_names_taken(everyone)
    temper = f"Make this villager {SOUR if rng.random() < SOUR_SHARE else GOOD}. "
    used = {_first(v.name) for v in everyone}
    for _ in range(NEWCOMER_TRIES):
        text = await writer.complete(SYSTEM, PROMPT.format(n=1, temper=temper, roles="", taken=taken), max_tokens=800)
        raw = extract_json(text)
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        raw = _prepare(raw)
        if not _valid(raw):
            raise ValueError(f"newcomer reply is not a valid villager: {text[:200]!r}")
        if _first(raw["name"]) not in used:
            break
    else:
        raise ValueError(f"newcomer reuses a first name: {raw['name']!r}")
    last = max((int(v.id[1:]) for v in everyone), default=0)
    newcomer = _villager(f"v{last + 1:03d}", raw)
    for other in rng.sample(existing, min(rng.randint(1, 2), len(existing))):
        newcomer.relationships[other.id] = rng.choice(
            [k for k in KINDS if k != "spouse"]
        )
    return newcomer
