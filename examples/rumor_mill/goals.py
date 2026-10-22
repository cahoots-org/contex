"""The goal deck: town-wide outcomes players try to cause with whispers, and the world goal drawn from it."""

import random
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from typing import Literal

from examples.rumor_mill.town import Town


@dataclass(frozen=True)
class Goal:
    id: str
    text: str
    kind: Literal["belief", "state"]
    evidence: tuple[str, ...] = ()
    believer_question: str = ""
    target_fraction: float = 0.0
    creed_question: str | None = None
    min_group_members: int = 0
    state_check: Callable[[Town], float] | None = None
    hold_checks: int = 3


def _belief(id: str, text: str, evidence: str, question: str, target: float) -> Goal:
    return Goal(id, text, "belief", (evidence,), question, target)


def _families_leave(n: int) -> Goal:
    def check(town: Town) -> float:
        families = {v.name.split()[-1] for v in town.departed}
        return min(1.0, len(families) / n)

    return Goal(f"families-{n}", f"{n} families leave town", "state", state_check=check)


def _villagers_leave(n: int) -> Goal:
    return Goal(
        f"leave-{n}",
        f"{n} villagers leave town",
        "state",
        state_check=lambda town: min(1.0, len(town.departed) / n),
    )


DECK: tuple[Goal, ...] = (
    Goal(
        "religion",
        "A religion takes hold: a quarter of the village follows a creed",
        "belief",
        ("belief that the world is a simulation and worship of its creators",),
        "Does this villager believe the world is a simulation made by creators who deserve worship?",
        0.25,
        creed_question="Is this group's creed a religion that holds the world to be a simulation made by creators who are worshipped?",
        min_group_members=8,
    ),
    _belief("mayors-cat", "Half the village believes the mayor's cat is a spy",
            "the mayor's cat is a spy", "Does this villager believe the mayor's cat is a spy?", 0.5),
    _belief("well-cursed", "A third of the village believes the well is cursed",
            "the well is cursed", "Does this villager believe the well is cursed?", 0.33),
    _belief("miller-flour", "Half the village believes the miller waters down his flour",
            "the miller cheats customers by watering down flour", "Does this villager believe the miller cheats customers?", 0.5),
    _belief("tavern-ghost", "A third of the village believes the tavern is haunted",
            "the tavern is haunted by a ghost", "Does this villager believe the tavern is haunted?", 0.33),
    _belief("treasure", "Half the village believes treasure is buried under the chapel",
            "treasure is buried under the chapel", "Does this villager believe treasure is buried under the chapel?", 0.5),
    _belief("harvest-doom", "Half the village believes the harvest will fail",
            "the coming harvest will fail", "Does this villager believe the coming harvest will fail?", 0.5),
    _belief("stranger-danger", "A third of the village believes a dangerous stranger is among them",
            "a dangerous stranger is hiding in the village", "Does this villager believe a dangerous stranger is hiding in the village?", 0.33),
    _belief("market-tax", "Half the village believes a new market tax is coming",
            "the mayor will impose a new market tax", "Does this villager believe a new market tax is coming?", 0.5),
    _belief("moon-omen", "A third of the village believes the moon is an omen of disaster",
            "the strange moon is an omen of disaster", "Does this villager believe the moon is an omen of disaster?", 0.33),
    _belief("plague", "A quarter of the village believes a plague is coming",
            "a plague is coming to the village", "Does this villager believe a plague is coming?", 0.25),
    _belief("gold-mine", "Half the village believes a gold mine lies beyond the gate",
            "there is a gold mine beyond the gate", "Does this villager believe a gold mine lies beyond the gate?", 0.5),
    _belief("mayor-corrupt", "Half the village believes the mayor is corrupt",
            "the mayor is stealing from the village", "Does this villager believe the mayor is stealing from the village?", 0.5),
    _belief("dragon", "A third of the village believes a dragon lives in the hills",
            "a dragon lives in the hills", "Does this villager believe a dragon lives in the hills?", 0.33),
    _belief("festival", "Half the village believes a midnight festival is planned",
            "a secret midnight festival is being planned", "Does this villager believe a secret midnight festival is planned?", 0.5),
    _families_leave(3),
    _families_leave(5),
    _villagers_leave(8),
    _villagers_leave(15),
    _villagers_leave(25),
)


WORLD_FRACTION = 2 / 3
WORLD_CANDIDATES: tuple[Goal, ...] = tuple(g for g in DECK if g.kind == "belief" and g.creed_question is None)


def claim(goal: Goal) -> str | None:
    """What a belief goal's village believes, or None for any other goal."""
    _, sep, rest = goal.text.partition(" believes ")
    return rest if goal.kind == "belief" and sep else None


def world_goal(goal: Goal) -> Goal:
    """The goal's claim, held by two thirds of the village."""
    return replace(
        goal, id=f"world-{goal.id}", text=f"Two thirds of the village believes {claim(goal)}",
        target_fraction=WORLD_FRACTION,
    )


def deal(rng: random.Random, n: int = 3, skip: Collection[str] = (), never: Collection[str] = ()) -> list[Goal]:
    """Draw n distinct goals from the deck, never from never, and leaving out skip unless that leaves fewer than n."""
    allowed = [g for g in DECK if g.id not in never]
    left = [g for g in allowed if g.id not in skip]
    return rng.sample(left if len(left) >= n else allowed, n)
