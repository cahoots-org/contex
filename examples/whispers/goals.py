"""Goals: what the village must come to believe, how many must believe it, and the trophies won for it."""

import math
from dataclasses import dataclass, replace
from typing import Literal

from examples.whispers.models import Villager
from examples.whispers.town import Town


@dataclass(frozen=True)
class Goal:
    id: str
    text: str
    kind: Literal["belief", "action"]
    evidence: tuple[str, ...] = ()
    believer_question: str = ""
    target_fraction: float = 0.0
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
    # What a villager must do for an action goal: confront, leave_town, found_group, join_group or exodus.
    action: str | None = None
    # The villager who must do it, where the goal names one.
    actor: str | None = None
    # The subject id, or the group id for join_group.
    target: str | None = None
    # How many times the action must happen.
    count: int = 1


MIN_CIRCLE = 3
WORLD_FRACTION = 2 / 3


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
    if goal.action == "join_group":
        group = next((g for g in town.groups if g.id == goal.target and g.members), None)
        if group is None:
            return "That group has broken up"
        if goal.actor in group.members:
            return f"{goal.audience_label} has already joined"
    if len(goal.audience) > 1 and len(audience_of(goal, town)) < MIN_CIRCLE:
        return f"too few of {goal.audience_label} are left"
    return None


def goal_counts(goal: Goal, known: dict[str, tuple[int, int]], villagers: int) -> tuple[int, int] | None:
    """A belief goal's believers and how many it needs, none of the whole village before its first check.

    None for a goal that is not a belief goal, and for an unchecked goal while the town is still empty.
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


def world_goal(goal: Goal) -> Goal:
    """The goal's claim, held by two thirds of the village."""
    return replace(goal, text=f"Two thirds of the village believes {claim(goal)}", target_fraction=WORLD_FRACTION)


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


def goal_targets(goal: Goal) -> dict:
    """What the page needs to mark a goal's targets: its long shot, the villagers it works on or who must act, and the one it is about."""
    marks = list(goal.audience) if goal.subject or goal.actor else []
    return {"long_shot": goal.long_shot, "marks": marks, "subject": goal.subject, "action": goal.kind == "action"}
