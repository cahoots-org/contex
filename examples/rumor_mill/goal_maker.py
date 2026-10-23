"""The goal maker: fills each goal's template from the village's people, circles and pool, with Jev's picks."""

import asyncio
import logging
import random
import re
from collections.abc import Collection
from dataclasses import dataclass, replace

from typesafe_sdk import Choice

from examples.rumor_mill.config import Config
from examples.rumor_mill.decide import MAX_OPTIONS
from examples.rumor_mill.game import PHASES
from examples.rumor_mill.goals import BANDS, MIN_CIRCLE, Goal
from examples.rumor_mill.lineage import Ids
from examples.rumor_mill.models import Villager
from examples.rumor_mill.pool import Entry, Pool
from examples.rumor_mill.routine import ROUTINE_PLACES
from examples.rumor_mill.town import START_PLACE, Town
from examples.rumor_mill.usage import charging

log = logging.getLogger(__name__)

HAND = 3
TEMPLATES = ("personal", "circle", "village")
# How much likelier a villager named in another's grudge, or holding a rival tie, is to be a goal's subject.
GRUDGE_WEIGHT = 3
REGULAR_PLACES = tuple(p for p in ROUTINE_PLACES if p != START_PLACE)
THE = re.compile(r"^the\s+", re.IGNORECASE)

ACCUSATION_INSTRUCTIONS = (
    "A newcomer to a gossiping village means to spread a rumor about this villager. Given their trade, temper "
    "and the grudges held against them, which accusation would ring truest of them?"
)
MARK_INSTRUCTIONS = "Which villager has the most reason to believe this rumor of the subject?"
CIRCLE_INSTRUCTIONS = "Which circle of villagers would care most about this rumor of the subject?"


def _first(villager: Villager) -> str:
    return villager.name.split()[0]


def _names(villager: Villager, text: str) -> bool:
    """Whether text names the villager by first name, as a whole word."""
    return re.search(rf"\b{re.escape(_first(villager))}\b", text, re.IGNORECASE) is not None


def _called(villager: Villager, town: Town) -> str:
    """How the player is told of the villager: by first name, or full name when another in town shares it."""
    shared = sum(_first(v) == _first(villager) for v in town.villagers) > 1
    return villager.name if shared else _first(villager)


@dataclass(frozen=True)
class Audience:
    """Who a goal works on: one mark or a circle."""

    id: str
    label: str
    members: tuple[str, ...]
    # How Jev is told of it.
    about: str


@dataclass(frozen=True)
class Slot:
    """One goal's template and the options for its blanks, with the ones picked."""

    kind: str
    entries: tuple[Entry, ...]
    entry: Entry
    subject: Villager | None = None
    audiences: tuple[Audience, ...] = ()
    audience: Audience | None = None


class GoalMaker:
    def __init__(
        self, jev, pool: Pool, town: Town, routine, overhear, baselines, config: Config, rng: random.Random, ids: Ids,
    ):
        self.jev = jev
        self.pool = pool
        self.town = town
        self.routine = routine
        self.overhear = overhear
        self.baselines = baselines
        self.config = config
        self.rng = rng
        self.ids = ids
        # Goals drawn but not yet given baselines and published.
        self._unready: list[Goal] = []

    def draw(self, player_id: str, taken: set[str], size: int = HAND, subjects: Collection[str] = ()) -> list[Goal]:
        """Up to size goals for the player, filled by uniform picks; their evidence goes out on the next prepare."""
        made = [self._goal(s, player_id) for s in self._slots(taken, size, subjects)]
        self._unready.extend(made)
        return made

    async def hand(
        self, player_id: str, taken: set[str], size: int = HAND, subjects: Collection[str] = (),
    ) -> list[Goal]:
        """Up to size goals for the player, filled by Jev's picks, with their baselines loaded and evidence published.

        taken holds the lowercased claims already in play; subjects the villagers the player's other goals are about.
        """
        with charging(player_id):
            slots = await asyncio.gather(*(self._refine(s) for s in self._slots(taken, size, subjects)))
            made = [self._goal(s, player_id) for s in slots]
            await self._ready(made)
        return made

    async def prepare(self) -> None:
        """Load baselines for, and publish, the goals drawn since the last call."""
        goals, self._unready = self._unready, []
        if goals:
            await self._ready(goals)

    async def _ready(self, goals: list[Goal]) -> None:
        try:
            await self.baselines.load([g.claim_text for g in goals], persist=False)
            await self.overhear.publish(goals)
        except Exception:
            log.warning("readying goals failed; retrying on the next check", exc_info=True)
            self._unready.extend(goals)

    def _slots(self, taken: Collection[str], size: int, subjects: Collection[str]) -> list[Slot]:
        """Size slots, each of a template drawn uniformly from those that can still be filled; fewer if none can."""
        taken, subjects = set(taken), set(subjects)
        slots: list[Slot] = []
        for _ in range(size):
            fillable = [s for kind in TEMPLATES if (s := self._fill(kind, taken, subjects)) is not None]
            if not fillable:
                break
            slot = self.rng.choice(fillable)
            slots.append(slot)
            taken.add(self._claim(slot).lower())
            if slot.subject:
                subjects.add(slot.subject.id)
        return slots

    def _fill(self, kind: str, taken: set[str], subjects: set[str]) -> Slot | None:
        """A slot of the template filled by uniform picks, or None when it can't be filled."""
        if kind == "village":
            claims = tuple(e for e in self.pool.claims if e.text.lower() not in taken)
            return Slot(kind, claims, self.rng.choice(claims)) if claims else None
        villagers = self.town.villagers
        circles = self._circles() if kind == "circle" else []
        candidates = []
        for v in villagers:
            if v.id in subjects:
                continue
            entries = tuple(e for e in self.pool.accusations if f"{self._call(v)} {e.text}".lower() not in taken)
            audiences = self._marks(v) if kind == "personal" else self._without(v, circles)
            if entries and audiences:
                candidates.append((v, entries, audiences))
        if not candidates:
            return None
        targets = self._targets(villagers)
        weights = [GRUDGE_WEIGHT if v.id in targets else 1 for v, _, _ in candidates]
        v, entries, audiences = self.rng.choices(candidates, weights=weights)[0]
        return Slot(kind, entries, self.rng.choice(entries), v, audiences, self.rng.choice(audiences))

    def _targets(self, villagers: list[Villager]) -> set[str]:
        """The villagers named in another's grudge or holding a rival tie."""
        return {
            v.id for v in villagers
            if "rival" in v.relationships.values() or any(o.id != v.id and _names(v, o.grudge) for o in villagers)
        }

    def _marks(self, subject: Villager) -> tuple[Audience, ...]:
        """Every other villager in town, as a one-person audience."""
        return tuple(
            Audience(v.id, self._call(v), (v.id,), self._sketch(v, subject))
            for v in self.town.villagers if v.id != subject.id
        )

    def _circles(self) -> list[Audience]:
        """The founded groups and each place's regulars, whole."""
        circles = [
            Audience(g.id, f"the {THE.sub('', g.name)}", tuple(sorted(g.members)), f"{g.name}, whose creed is: {g.creed}")
            for g in self.town.groups
        ]
        for place in REGULAR_PLACES:
            members = tuple(
                v.id for v in self.town.villagers if any(self.routine.place(v.id, ph) == place for ph in PHASES)
            )
            circles.append(Audience(f"regulars:{place}", f"the {place} regulars", members,
                                    f"those who spend their days or evenings at the {place}"))
        return circles

    def _without(self, subject: Villager, circles: list[Audience]) -> tuple[Audience, ...]:
        """The circles left with MIN_CIRCLE or more members once the subject is taken out."""
        out = (replace(c, members=tuple(m for m in c.members if m != subject.id)) for c in circles)
        return tuple(c for c in out if len(c.members) >= MIN_CIRCLE)

    def _call(self, v: Villager) -> str:
        return _called(v, self.town)

    def _sketch(self, v: Villager, subject: Villager) -> str:
        line = f"{v.name}, {v.occupation}, {v.personality}"
        if v.grudge:
            line += f". Grudge: {v.grudge}"
        if tie := v.relationships.get(subject.id):
            line += f". The subject's {tie}"
        return line

    def _claim(self, slot: Slot) -> str:
        return f"{self._call(slot.subject)} {slot.entry.text}" if slot.subject else slot.entry.text

    async def _refine(self, slot: Slot) -> Slot:
        """The slot with Jev's accusation and then its mark or circle; a pick Jev fails keeps the uniform one."""
        if self.jev is None or slot.subject is None:
            return slot
        subject = slot.subject
        state = {"subject": {
            "name": subject.name, "occupation": subject.occupation, "personality": subject.personality,
            "grudge": subject.grudge,
            "held_against_them": [v.grudge for v in self.town.villagers if v.id != subject.id and _names(subject, v.grudge)],
        }}
        entries = {str(i): e.text for i, e in enumerate(slot.entries)}
        if (key := await self._choose(ACCUSATION_INSTRUCTIONS, state, entries)) is not None:
            slot = replace(slot, entry=slot.entries[int(key)])
        state["rumor"] = self._claim(slot)
        instructions = MARK_INSTRUCTIONS if slot.kind == "personal" else CIRCLE_INSTRUCTIONS
        if (key := await self._choose(instructions, state, {a.id: a.about for a in slot.audiences})) is not None:
            slot = replace(slot, audience=next(a for a in slot.audiences if a.id == key))
        return slot

    async def _choose(self, instructions: str, state: dict, options: dict[str, str]) -> str | None:
        """The key of the option Jev picks, or None if the request fails."""
        criteria = dict(list(options.items())[:MAX_OPTIONS])
        try:
            response = await self.jev.system_one(
                state=state, questions={"pick": Choice(instructions=instructions, criteria=criteria)},
            )
            choice = response.choices["pick"].choice
        except Exception:
            log.warning("Jev could not fill a goal", exc_info=True)
            return None
        return choice if choice in criteria else None

    def _goal(self, slot: Slot, player_id: str) -> Goal:
        """The slot's goal, numbered for the player, with its bar from its entry's believability band."""
        claim = self._claim(slot)
        fraction, word = BANDS[slot.entry.band]
        goal = Goal(
            id=f"gen-{player_id}-{self.ids.next('goal')}", text="", kind="belief", evidence=(claim,),
            believer_question=f"Does this villager believe {claim}?", owner=player_id, claim_text=claim,
            long_shot=slot.entry.band == 0,
        )
        if slot.subject is None:
            return replace(goal, text=f"{word} of the village believes {claim}", target_fraction=fraction)
        audience = slot.audience
        goal = replace(goal, subject=slot.subject.id, audience=audience.members, audience_label=audience.label)
        if slot.kind == "personal":
            return replace(goal, text=f"Convince {audience.label} that {claim}")
        return replace(goal, text=f"Get {word.lower()} of {audience.label} to believe {claim}", target_fraction=fraction)
