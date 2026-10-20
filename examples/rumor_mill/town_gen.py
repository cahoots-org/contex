"""Generate the village: Haiku writes the people, code wires who knows whom."""

import json
import random
import re
from dataclasses import asdict

from examples.rumor_mill.models import Villager
from examples.rumor_mill.writer import Writer, extract_json

BATCH = 10
MAX_BATCH_ATTEMPTS = 5
REQUIRED = ("name", "occupation", "personality", "grudge", "needs")
MAX_RELATIONSHIPS = 4
REVERSE = {
    "spouse": "spouse", "rival": "rival", "friend": "friend",
    "debtor": "creditor", "creditor": "debtor",
}
# Weighted draw, so friends are commoner than spouses.
KINDS = ["friend", "friend", "rival", "debtor", "creditor", "spouse"]

SYSTEM = (
    "You invent villagers for a small medieval village. Reply with only JSON, no prose."
)
PROMPT = (
    "Invent {n} distinct villagers as a top-level JSON array of objects, not wrapped in "
    "another object. Each item has: name (a full name), occupation, personality (one "
    "short phrase), grudge (one sentence about someone or something they resent), and "
    "needs: a JSON array of 3 to 5 strings, plain-English sentences written in the "
    "villager's own voice about their own specific concerns: their trade, family, fears, "
    "hobbies, grudges. Make needs particular to this person and different from the other "
    "villagers' where possible, never generic topics like news or gossip. {taken}"
)


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


def _villager(vid: str, raw: dict) -> Villager:
    return Villager(
        id=vid, name=raw["name"], occupation=raw["occupation"],
        personality=raw["personality"], grudge=raw["grudge"],
        needs=list(raw["needs"])[:5], relationships={},
    )


async def generate_villagers(writer: Writer, count: int, rng: random.Random) -> list[Villager]:
    """Ask Haiku for villagers in batches of 10, with ids v001... and no relationships."""
    villagers: list[Villager] = []
    for _ in range(MAX_BATCH_ATTEMPTS):
        if len(villagers) >= count:
            break
        n = min(BATCH, count - len(villagers))
        taken = ""
        if villagers:
            names = ", ".join(v.name for v in villagers[-30:])
            taken = f"Do not reuse these names or occupations: {names}."
        text = await writer.complete(SYSTEM, PROMPT.format(n=n, taken=taken), max_tokens=4000)
        try:
            parsed = extract_json(text)
        except ValueError:
            continue
        items = _unwrap(parsed)
        if items is None:
            continue
        for raw in [r for r in map(_prepare, items) if _valid(r)][:n]:
            villagers.append(_villager(f"v{len(villagers) + 1:03d}", raw))
    if len(villagers) < count:
        raise ValueError(
            f"got {len(villagers)} of {count} villagers after {MAX_BATCH_ATTEMPTS} attempts"
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


async def load_or_generate(
    path: str, writer: Writer, count: int, rng: random.Random,
) -> list[Villager]:
    """Load the seed file if present, else generate, wire, and write it."""
    try:
        with open(path) as f:
            return [Villager(**raw) for raw in json.load(f)]
    except FileNotFoundError:
        pass
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
    taken = "Do not reuse these names: " + ", ".join(v.name for v in everyone[-30:]) + "."
    text = await writer.complete(SYSTEM, PROMPT.format(n=1, taken=taken), max_tokens=800)
    raw = extract_json(text)
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    raw = _prepare(raw)
    if not _valid(raw):
        raise ValueError(f"newcomer reply is not a valid villager: {text[:200]!r}")
    last = max((int(v.id[1:]) for v in everyone), default=0)
    newcomer = _villager(f"v{last + 1:03d}", raw)
    for other in rng.sample(existing, min(rng.randint(1, 2), len(existing))):
        newcomer.relationships[other.id] = rng.choice(
            [k for k in KINDS if k != "spouse"]
        )
    return newcomer
