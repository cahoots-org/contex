"""The goal maker: has Haiku write each hand's personal and circle goals about real villagers."""

import asyncio
import logging
import random
import re
from collections.abc import Collection
from dataclasses import replace

from examples.rumor_mill.config import Config
from examples.rumor_mill.goals import BANDS, MIN_CIRCLE, Goal, deal
from examples.rumor_mill.lineage import Ids
from examples.rumor_mill.models import Villager
from examples.rumor_mill.town import Town
from examples.rumor_mill.usage import charging

log = logging.getLogger(__name__)

HAND = 3
# The longest field of a goal Haiku may write.
MAX_FIELD = 200


def _field(raw: dict, key: str) -> str:
    """The raw goal's string field, or "" when missing, not a string, or longer than MAX_FIELD."""
    value = raw.get(key)
    return value.strip() if isinstance(value, str) and len(value.strip()) <= MAX_FIELD else ""


def _first(villager: Villager) -> str:
    return villager.name.split()[0]


def _names(villager: Villager, text: str) -> bool:
    """Whether text names the villager by first name, as a whole word."""
    return re.search(rf"\b{re.escape(_first(villager))}\b", text, re.IGNORECASE) is not None


def _one(town: Town, name: str) -> Villager | None:
    """The one villager in town with this full name, or None."""
    found = [v for v in town.villagers if v.name.lower() == name.lower()]
    return found[0] if len(found) == 1 else None


def _circle(spec, town: Town) -> list[Villager]:
    """The villagers in town the circle spec names, or [] if it names none."""
    if not isinstance(spec, dict) or not isinstance(spec.get("value"), str):
        return []
    by, value = spec.get("by"), spec["value"].strip().lower()
    if by == "occupation":
        return [v for v in town.villagers if v.occupation.lower() == value]
    if by == "family":
        return [v for v in town.villagers if v.name.split()[-1].lower() == value]
    if by == "ties" and (hub := _one(town, value)):
        return [town.get(vid) for vid in hub.relationships if vid in town]
    return []


def _validate(raw: dict, town: Town, taken: Collection[str]) -> Goal | None:
    """The raw goal as an unnumbered, unbanded Goal, or None if it names anyone not in town or breaks a rule."""
    claim, question = _field(raw, "claim"), _field(raw, "question")
    subject = _one(town, _field(raw, "subject"))
    if not (claim and question and subject) or not _names(subject, claim):
        return None
    if claim.lower() in {t.lower() for t in taken}:
        return None
    base = Goal(
        id="", text="", kind="belief", evidence=(claim,), believer_question=question, subject=subject.id,
        claim_text=claim,
    )
    if raw.get("kind") == "personal":
        mark, text = _one(town, _field(raw, "mark")), _field(raw, "text")
        if mark is None or mark.id == subject.id or not _names(mark, text):
            return None
        return replace(base, text=text, audience=(mark.id,), audience_label=_first(mark))
    label = _field(raw, "label")
    members = [v.id for v in _circle(raw.get("circle"), town) if v.id != subject.id]
    if not label or len(members) < MIN_CIRCLE:
        return None
    return replace(base, audience=tuple(members), audience_label=label)


class GoalMaker:
    def __init__(self, writer, believability, baselines, overhear, town: Town, config: Config, rng: random.Random,
                 ids: Ids):
        self.writer = writer
        self.believability = believability
        self.baselines = baselines
        self.overhear = overhear
        self.town = town
        self.config = config
        self.rng = rng
        self.ids = ids

    async def hand(self, player_id: str, taken: set[str], deck_skip: set[str], never: set[str]) -> list[Goal]:
        """The player's personal and circle goals, then deck goals to make three; all deck goals if Haiku fails."""
        made: list[Goal] = []
        with charging(player_id):
            try:
                made = await asyncio.wait_for(
                    self._write(player_id, taken), self.config.goal_writer_timeout_seconds,
                )
                await self.baselines.load([g.claim_text for g in made], persist=False)
                await self.overhear.publish(made)
            except Exception:
                log.warning("writing goals for %s failed", player_id, exc_info=True)
                self.overhear.drop([g.id for g in made])
                made = []
        return made + deal(self.rng, HAND - len(made), skip=deck_skip, never=never)

    async def _write(self, player_id: str, taken: set[str]) -> list[Goal]:
        """The first valid personal and circle goals Haiku writes for the player, banded by believability."""
        raws = await self.writer.goals(self._sample(), sorted(taken))
        made: list[Goal] = []
        for kind in ("personal", "circle"):
            for raw in (r for r in raws if r.get("kind") == kind):
                goal = _validate(raw, self.town, taken | {g.claim_text for g in made})
                if goal is not None:
                    made.append(await self._finish(goal, player_id))
                    break
        return made

    async def _finish(self, goal: Goal, player_id: str) -> Goal:
        """The goal numbered for the player, with its bar set by how believable its claim is."""
        band = await self.believability.band(goal.claim_text)
        goal = replace(goal, id=f"gen-{player_id}-{self.ids.next('goal')}", owner=player_id, long_shot=band == 0)
        if len(goal.audience) == 1:
            return goal
        fraction, word = BANDS[band]
        return replace(
            goal, target_fraction=fraction,
            text=f"Get {word.lower()} of {goal.audience_label} to believe {goal.claim_text}",
        )

    def _sample(self) -> list[Villager]:
        """Up to goal_sample_size villagers in town, those with a grudge or ties first."""
        shuffled = self.rng.sample(self.town.villagers, len(self.town.villagers))
        return sorted(shuffled, key=lambda v: not (v.grudge or v.relationships))[:self.config.goal_sample_size]
