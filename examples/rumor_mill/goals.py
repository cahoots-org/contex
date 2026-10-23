"""The goal deck: town-wide outcomes players try to cause with whispers, and the world goal drawn from it."""

import math
import random
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from typing import Literal

from examples.rumor_mill.models import Villager
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
    # The villager ids that count, or the whole village when empty.
    audience: tuple[str, ...] = ()
    audience_label: str = ""
    # The villager the claim is about.
    subject: str | None = None
    # The player a generated goal belongs to.
    owner: str | None = None
    claim_text: str = ""
    long_shot: bool = False


def _belief(id: str, text: str, evidence: str, question: str, target: float) -> Goal:
    return Goal(id, text, "belief", (evidence,), question, target, claim_text=text.partition(" believes ")[2])


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


MIN_CIRCLE = 3
WORLD_FRACTION = 2 / 3
WORLD_CANDIDATES: tuple[Goal, ...] = tuple(g for g in DECK if g.kind == "belief" and g.creed_question is None)


def needed(goal: Goal, counted: int) -> int:
    """How many of the counted villagers must believe a belief goal's claim to meet it."""
    if len(goal.audience) == 1:
        return 1
    return math.ceil(goal.target_fraction * counted)


def audience_of(goal: Goal, town: Town) -> list[Villager]:
    """The villagers in town who count toward the goal: its audience, or the whole village when it has none."""
    if not goal.audience:
        return town.villagers
    return [town.get(vid) for vid in goal.audience if vid in town]


def void_reason(goal: Goal, town: Town) -> str | None:
    """Why the goal can no longer be met, or None if it still can."""
    gone = [vid for vid in (goal.subject, *(goal.audience if len(goal.audience) == 1 else ())) if vid and vid not in town]
    if gone:
        name = next((v.name for v in town.departed if v.id == gone[0]), "A villager")
        return f"{name} has left town"
    if len(goal.audience) > 1 and len(audience_of(goal, town)) < MIN_CIRCLE:
        return f"too few of {goal.audience_label} are left"
    return None


def goal_counts(goal: Goal, known: dict[str, tuple[int, int]], villagers: int) -> tuple[int, int] | None:
    """A belief goal's believers and how many it needs, none of the whole village before its first check.

    None for a state goal, which has only a percent, and for an unchecked goal while the town is still empty.
    """
    if goal.kind != "belief":
        return None
    if goal.id in known or not villagers:
        return known.get(goal.id)
    return 0, needed(goal, len(goal.audience) or villagers)


def claim(goal: Goal) -> str | None:
    """What a belief goal's village believes, or None for any other goal."""
    if goal.claim_text:
        return goal.claim_text
    _, sep, rest = goal.text.partition(" believes ")
    return rest if goal.kind == "belief" and sep else None


BANDS: tuple[tuple[float, str], ...] = ((1 / 5, "A fifth"), (1 / 4, "A quarter"), (1 / 3, "A third"), (1 / 2, "Half"))


def rebar(goal: Goal, band: int) -> Goal:
    """A village-wide belief goal with its bar set by the believability band, 0 being a long shot."""
    if goal.kind != "belief" or goal.creed_question or goal.audience:
        return goal
    fraction, word = BANDS[band]
    return replace(
        goal, target_fraction=fraction, text=f"{word} of the village believes {claim(goal)}", long_shot=band == 0,
    )


async def rebar_deck(believability) -> None:
    """Set each village-wide belief goal's bar in DECK and WORLD_CANDIDATES from how believable its claim is."""
    global DECK, WORLD_CANDIDATES
    DECK = tuple([rebar(g, await believability.band(claim(g))) if claim(g) else g for g in DECK])
    WORLD_CANDIDATES = tuple(g for g in DECK if g.kind == "belief" and g.creed_question is None)


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


@dataclass(frozen=True)
class Trophy:
    kind: Literal["personal", "world"]
    # The texts of the goals it was won for.
    goals: tuple[str, ...]
    day: int
    long_shot: bool = False

    def to_json(self) -> dict:
        return {"kind": self.kind, "goals": list(self.goals), "day": self.day, "long_shot": self.long_shot}


def trophy_line(trophy: Trophy, claim: str | None, unseen: str | None = None) -> str:
    """What the player is told on winning the trophy, naming the claim the village now holds and who never knew."""
    if trophy.kind == "world":
        return f"The whole village now holds that {claim}. A world trophy for your case."
    if claim is None:
        line = "The village is not what it was, and it all began with a whisper. A trophy for your case."
    else:
        line = f"The village now swears {claim}, and it all began with a whisper. A trophy for your case."
    return f"{line} And {unseen} never knew." if unseen else line


def kind_of(goal: Goal) -> str | None:
    """"personal" or "circle" for a generated goal about a villager, None for a deck goal."""
    if not goal.subject:
        return None
    return "personal" if len(goal.audience) == 1 else "circle"


def goal_targets(goal: Goal) -> dict:
    """What the page needs to mark a goal's targets: its long shot, the villagers it works on and the one it is about."""
    return {"long_shot": goal.long_shot, "marks": list(goal.audience) if goal.subject else [], "subject": goal.subject}
