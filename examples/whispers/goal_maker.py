"""The goal maker: fills each goal's template from the village's people, circles and pool, with Jev's picks."""

import asyncio
import logging
import random
import re
from collections.abc import Collection
from dataclasses import dataclass, replace

from typesafe_sdk import Choice

from examples.whispers.config import Config
from examples.whispers.decide import MAX_OPTIONS
from examples.whispers.game import PHASES
from examples.whispers.goals import BANDS, MIN_CIRCLE, Goal, world_goal
from examples.whispers.lineage import Ids
from examples.whispers.models import Group, Villager
from examples.whispers.pool import Entry, Pool
from examples.whispers.routine import ROUTINE_PLACES
from examples.whispers.town import START_PLACE, Town
from examples.whispers.usage import charging

log = logging.getLogger(__name__)

HAND = 3
CLAIM_TEMPLATES = ("personal", "circle", "village")
ACTION_TEMPLATES = ("confront", "leave_town", "found_group", "join_group", "exodus")
TEMPLATES = CLAIM_TEMPLATES + ACTION_TEMPLATES
EXODUS_SIZES = (2, 3)
LONG_SHOT_ACTIONS = ("leave_town", "exodus")
# Action templates filled by a subject alone.
SUBJECT_ONLY = ("leave_town", "found_group")
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
CONFRONT_INSTRUCTIONS = "Which villager would most plausibly confront the subject?"
JOIN_INSTRUCTIONS = "Which villager would most plausibly join this group?"


def _first(villager: Villager) -> str:
    return villager.name.split()[0]


def names(villager: Villager, text: str) -> bool:
    """Whether text names the villager by first name, as a whole word."""
    return re.search(rf"\b{re.escape(_first(villager))}\b", text, re.IGNORECASE) is not None


def _key(slot: "Slot") -> tuple[str, str | None]:
    """The action a slot's goal would ask for and its target, as held goals give (action, target)."""
    return slot.kind, slot.group.id if slot.group else slot.subject and slot.subject.id


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
    """One goal's template and the options for its blanks, with the ones picked.

    For an action template the audiences are the candidate actors.
    """

    kind: str
    entries: tuple[Entry, ...] = ()
    entry: Entry | None = None
    subject: Villager | None = None
    audiences: tuple[Audience, ...] = ()
    audience: Audience | None = None
    group: Group | None = None
    count: int = 1


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

    def draw(
        self, player_id: str, taken: set[str], size: int = HAND, subjects: Collection[str] = (),
        held: Collection[Goal] = (),
    ) -> list[Goal]:
        """Up to size goals for the player, filled by uniform picks; their evidence goes out on the next prepare."""
        made = [self._goal(s, player_id, self._id(player_id)) for s in self._slots(taken, size, subjects, held)]
        self._unready.extend(made)
        return made

    async def hand(
        self, player_id: str, taken: set[str], size: int = HAND, subjects: Collection[str] = (),
        held: Collection[Goal] = (),
    ) -> list[Goal]:
        """Up to size goals for the player, filled by Jev's picks, with their baselines loaded and evidence published.

        taken holds the lowercased claims already in play; subjects the villagers the player's other goals are about;
        held the player's other goals, whose actions are not drawn again.
        """
        with charging(player_id):
            slots = await asyncio.gather(*(self._refine(s) for s in self._slots(taken, size, subjects, held)))
            made = [self._goal(s, player_id, self._id(player_id)) for s in slots]
            await self._ready(made)
        return made

    def world_goal(self, taken: set[str]) -> Goal:
        """A pool claim no one holds in taken, for two thirds of the village; any claim when all are taken."""
        claims = tuple(e for e in self.pool.claims if e.text.lower() not in taken) or self.pool.claims
        goal = world_goal(self._goal(Slot("village", claims, self.rng.choice(claims)), None, self.ids.next("world")))
        self._unready.append(goal)
        return goal

    async def prepare(self) -> None:
        """Load baselines for, and publish, the goals drawn since the last call."""
        goals, self._unready = self._unready, []
        if goals:
            await self._ready(goals)

    async def _ready(self, goals: list[Goal]) -> None:
        try:
            await self.baselines.load([g.claim_text for g in goals if g.claim_text], persist=False)
            await self.overhear.publish(goals)
        except Exception:
            log.warning("readying goals failed; retrying on the next check", exc_info=True)
            self._unready.extend(goals)

    def _slots(self, taken: Collection[str], size: int, subjects: Collection[str], held: Collection[Goal]) -> list[Slot]:
        """Size slots, fewer if none can be filled.

        Each template is drawn from those that can still be filled: claims and actions carry half the weight each,
        shared evenly within each. No action is drawn twice at the same target, and Exodus at most once.
        """
        taken, subjects = set(taken), set(subjects)
        used = {(g.action, g.target) for g in held if g.action}
        slots: list[Slot] = []
        for _ in range(size):
            fillable = [
                s for kind in TEMPLATES
                if (s := self._fill(kind, taken, subjects, used)) is not None and _key(s) not in used
            ]
            if not fillable:
                break
            claims = sum(s.kind in CLAIM_TEMPLATES for s in fillable)
            weights = [1 / claims if s.kind in CLAIM_TEMPLATES else 1 / (len(fillable) - claims) for s in fillable]
            slot = self.rng.choices(fillable, weights=weights)[0]
            if slot.kind == "exodus":
                slot = replace(slot, count=self.rng.choice([n for n in EXODUS_SIZES if n <= len(self.town.villagers)]))
            slots.append(slot)
            if slot.kind in ACTION_TEMPLATES:
                used.add(_key(slot))
            if slot.entry:
                taken.add(self._claim(slot).lower())
            if slot.subject:
                subjects.add(slot.subject.id)
        return slots

    def _fill(self, kind: str, taken: set[str], subjects: set[str], used: set[tuple]) -> Slot | None:
        """A slot of the template filled by uniform picks, or None when it can't be filled."""
        if kind == "village":
            claims = tuple(e for e in self.pool.claims if e.text.lower() not in taken)
            return Slot(kind, claims, self.rng.choice(claims)) if claims else None
        villagers = self.town.villagers
        if kind == "exodus":
            return Slot(kind) if len(villagers) >= min(EXODUS_SIZES) else None
        if kind == "join_group":
            return self._join(villagers, used)
        circles = self._circles() if kind == "circle" else []
        candidates = []
        for v in villagers:
            if v.id in subjects:
                continue
            entries: tuple[Entry, ...] = ()
            audiences: tuple[Audience, ...] = ()
            if kind in CLAIM_TEMPLATES:
                entries = tuple(e for e in self.pool.accusations if f"{self._call(v)} {e.text}".lower() not in taken)
                audiences = self._marks(v) if kind == "personal" else self._without(v, circles)
                if not entries:
                    continue
            elif kind == "confront":
                audiences = self._marks(v)
            if audiences or kind in SUBJECT_ONLY:
                candidates.append((v, entries, audiences))
        if not candidates:
            return None
        targets = self._targets(villagers)
        weights = [GRUDGE_WEIGHT if v.id in targets else 1 for v, _, _ in candidates]
        v, entries, audiences = self.rng.choices(candidates, weights=weights)[0]
        entry = self.rng.choice(entries) if entries else None
        return Slot(kind, entries, entry, v, audiences, self.rng.choice(audiences) if audiences else None)

    def _join(self, villagers: list[Villager], used: set[tuple]) -> Slot | None:
        """A join slot: a group of two or more in town no goal has yet, with those outside it as candidate actors."""
        options = []
        for g in self.town.groups:
            outside = tuple(
                Audience(v.id, self._call(v), (v.id,), self._sketch(v)) for v in villagers if v.id not in g.members
            )
            if len(g.members) >= 2 and outside and ("join_group", g.id) not in used:
                options.append((g, outside))
        if not options:
            return None
        group, outside = self.rng.choice(options)
        return Slot("join_group", audiences=outside, audience=self.rng.choice(outside), group=group)

    def _targets(self, villagers: list[Villager]) -> set[str]:
        """The villagers named in another's grudge or holding a rival tie."""
        return {
            v.id for v in villagers
            if "rival" in v.relationships.values() or any(o.id != v.id and names(v, o.grudge) for o in villagers)
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
        """How the player is told of the villager: by full name, so they can find them."""
        return v.name

    def _sketch(self, v: Villager, subject: Villager | None = None) -> str:
        line = f"{v.name}, {v.occupation}, {v.personality}"
        if v.grudge:
            line += f". Grudge: {v.grudge}"
        if subject and (tie := v.relationships.get(subject.id)):
            line += f". The subject's {tie}"
        return line

    def _claim(self, slot: Slot) -> str:
        return f"{self._call(slot.subject)} {slot.entry.text}" if slot.subject else slot.entry.text

    async def _refine(self, slot: Slot) -> Slot:
        """The slot with Jev's accusation and then its mark, circle or actor; a pick Jev fails keeps the uniform one."""
        if self.jev is None or not slot.audiences:
            return slot
        if slot.group:
            group = {"name": slot.group.name, "creed": slot.group.creed}
            return await self._pick(slot, JOIN_INSTRUCTIONS, {"group": group})
        subject = slot.subject
        state = {"subject": {
            "name": subject.name, "occupation": subject.occupation, "personality": subject.personality,
            "grudge": subject.grudge,
            "held_against_them": [v.grudge for v in self.town.villagers if v.id != subject.id and names(subject, v.grudge)],
        }}
        if slot.kind == "confront":
            return await self._pick(slot, CONFRONT_INSTRUCTIONS, state)
        entries = {str(i): e.text for i, e in enumerate(slot.entries)}
        if (key := await self._choose(ACCUSATION_INSTRUCTIONS, state, entries)) is not None:
            slot = replace(slot, entry=slot.entries[int(key)])
        state["rumor"] = self._claim(slot)
        return await self._pick(slot, MARK_INSTRUCTIONS if slot.kind == "personal" else CIRCLE_INSTRUCTIONS, state)

    async def _pick(self, slot: Slot, instructions: str, state: dict) -> Slot:
        """The slot with Jev's pick of its audiences."""
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

    def _id(self, player_id: str) -> str:
        return f"gen-{player_id}-{self.ids.next('goal')}"

    def _goal(self, slot: Slot, owner: str | None, id: str) -> Goal:
        """The slot's goal, with its bar from its entry's believability band."""
        if slot.kind in ACTION_TEMPLATES:
            return self._action_goal(slot, owner, id)
        claim = self._claim(slot)
        fraction, word = BANDS[slot.entry.band]
        goal = Goal(
            id=id, text="", kind="belief", evidence=(claim,),
            believer_question=f"Does this villager believe {claim}?", owner=owner, claim_text=claim,
            long_shot=slot.entry.band == 0,
        )
        if slot.subject is None:
            return replace(goal, text=f"{word} of the village believes {claim}", target_fraction=fraction)
        audience = slot.audience
        goal = replace(goal, subject=slot.subject.id, audience=audience.members, audience_label=audience.label)
        if slot.kind == "personal":
            return replace(goal, text=f"Convince {audience.label} that {claim}")
        return replace(goal, text=f"Get {word.lower()} of {audience.label} to believe {claim}", target_fraction=fraction)

    def _action_goal(self, slot: Slot, owner: str | None, id: str) -> Goal:
        """The slot's action goal, with its actor as its audience when it names one."""
        goal = Goal(
            id=id, text="", kind="action", hold_checks=1, owner=owner, long_shot=slot.kind in LONG_SHOT_ACTIONS,
            action=slot.kind, count=slot.count,
        )
        if slot.audience:
            actor = slot.audience
            goal = replace(goal, actor=actor.id, audience=actor.members, audience_label=actor.label)
        if slot.subject:
            goal = replace(goal, subject=slot.subject.id, target=slot.subject.id)
            subject = self._call(slot.subject)
        if slot.kind == "confront":
            return replace(goal, text=f"Get {goal.audience_label} to confront {subject}")
        if slot.kind == "leave_town":
            return replace(goal, text=f"Drive {subject} out of town")
        if slot.kind == "found_group":
            return replace(goal, text=f"Get someone to start a group against {subject}")
        if slot.kind == "join_group":
            group = THE.sub("", slot.group.name)
            return replace(goal, text=f"Get {goal.audience_label} to join the {group}", target=slot.group.id)
        return replace(goal, text=f"Drive {slot.count} villagers out of town")
