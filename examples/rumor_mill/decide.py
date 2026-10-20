"""One Jev request per turn: what does this villager do, and to whom."""

import random
from dataclasses import dataclass

from typesafe_sdk import Choice

from examples.rumor_mill.actions import (
    ACTION_NOT_FOR, ACTIONS, IGNORE, JOIN_GROUP, STYLED, STYLES, TARGET_KIND,
    available_actions,
)
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.models import Group, Villager
from examples.rumor_mill.town import PLACES

MAX_OPTIONS = 255
MAX_RECENT = 10

ACTION_INSTRUCTIONS = (
    "You are deciding what one villager does next in a small village that runs on gossip. "
    "Judge from their personality, grudge, relationships, and what they just heard. "
    "Villagers relish news that touches their trade, family, standing, fears, or someone "
    "they love or resent, and usually pass it on, argue about it, or act on it. "
    "They do nothing about news that does not concern them, or that they have already "
    "heard: if the same story is among their recent memories, they have dealt with it. "
    "News that alarms or inspires them can move them to walk elsewhere, start or join a "
    "group, or leave town for good."
)


@dataclass
class TurnView:
    villager: Villager
    place: str
    present: list[Villager]
    member_groups: list[Group]
    visible_groups: list[Group]
    new: list[Match]
    recent: list[Match]


@dataclass(frozen=True)
class Decision:
    action: str
    source: Match | None
    target: str | None
    style: str | None


def _text(m: Match) -> str:
    return m.data.get("text", "")


def _person(v: Villager, other: Villager) -> str:
    kind = v.relationships.get(other.id)
    return f"{other.name}, {other.occupation}" + (f", their {kind}" if kind else "")


def _state(view: TurnView) -> dict:
    v = view.villager
    return {
        "villager": {
            "name": v.name, "occupation": v.occupation, "personality": v.personality,
            "grudge": v.grudge,
        },
        "relationships_here": {p.name: v.relationships[p.id] for p in view.present if p.id in v.relationships},
        "place": view.place,
        "present": [p.name for p in view.present],
        "just_heard": [_text(m) for m in view.new],
        "recent_memories": [_text(m) for m in view.recent[:MAX_RECENT]],
        "groups": [g.name for g in view.member_groups],
    }


def _action_criteria(view: TurnView) -> dict:
    out = {}
    for a in available_actions(view):
        note = ACTION_NOT_FOR.get(a)
        out[a] = {"what": ACTIONS[a], "not_for": note} if note else ACTIONS[a]
    return out


def _questions(view: TurnView) -> dict[str, Choice]:
    qs = {"action": Choice(instructions=ACTION_INSTRUCTIONS, criteria=_action_criteria(view))}
    if len(view.new) > 1:
        qs["source"] = Choice(
            instructions="Which of the things this villager just heard does the action respond to? "
            "Pick the one that matters most to them.",
            criteria={m.data_key: _text(m) for m in view.new},
        )
    if view.present:
        qs["villager_target"] = Choice(
            instructions="Which person nearby is this villager addressing?",
            # Villagers beyond the first 255 in `present` order are dropped.
            criteria={p.id: _person(view.villager, p) for p in view.present[:MAX_OPTIONS]},
        )
    qs["place_target"] = Choice(
        instructions="Which place does this villager walk to?",
        criteria={p: None for p in PLACES if p != view.place},
    )
    if view.visible_groups:
        qs["join_target"] = Choice(
            instructions="Which group, with members nearby, does this villager join?",
            criteria={g.id: f"{g.name}: {g.creed}" for g in view.visible_groups[:MAX_OPTIONS]},
        )
    if view.member_groups:
        qs["group_target"] = Choice(
            instructions="Which of this villager's own groups does the action concern?",
            criteria={g.id: f"{g.name}: {g.creed}" for g in view.member_groups[:MAX_OPTIONS]},
        )
    if any(a in STYLED for a in available_actions(view)):
        qs["style"] = Choice(
            instructions="In what manner would this villager say it, given their personality and what they heard?",
            criteria=dict(STYLES),
        )
    return qs


async def decide(jev, view: TurnView) -> Decision:
    """Ask Jev once and keep only the answers the chosen action needs.

    The action is drawn from Jev's probabilities, so a likely but not top
    choice still happens some of the time.
    """
    resp = await jev.system_one(state=_state(view), questions=_questions(view))
    pick = {k: c.choice for k, c in resp.choices.items()}
    odds = resp.choices["action"].probabilities
    action = random.choices(list(odds), weights=list(odds.values()))[0]
    if action == IGNORE:
        return Decision(IGNORE, None, None, None)
    if len(view.new) > 1:
        source = next(m for m in view.new if m.data_key == pick["source"])
    else:
        source = view.new[0] if view.new else None
    kind = TARGET_KIND.get(action)
    target_key = {
        "villager": "villager_target",
        "place": "place_target",
        "group": "join_target" if action == JOIN_GROUP else "group_target",
    }.get(kind)
    target = pick.get(target_key) if target_key else None
    style = pick.get("style") if action in STYLED else None
    return Decision(action, source, target, style)
