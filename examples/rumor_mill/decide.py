"""One Jev request per turn: what does this villager do, and to whom."""

import logging
import random
from dataclasses import dataclass, field

from typesafe_sdk import Choice, Score

from examples.rumor_mill.actions import (
    ACTION_NOT_FOR, ACTIONS, ANSWER, IGNORE, JOIN_GROUP, MOVE, STYLED, STYLES, TARGET_KIND,
    available_actions,
)
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.models import Group, Villager
from examples.rumor_mill.town import PLACES

log = logging.getLogger(__name__)

MAX_OPTIONS = 255
# Actions Jev rates below this are never drawn.
MIN_ACTION_PROBABILITY = 0.1
MAX_RECENT = 10
MAX_SAID = 3

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

SUSPICION_LEVELS = ["not at all", "a little", "quite a bit", "convinced"]
GOSSIP_LEVELS = ["keeps news to themselves", "shares now and then", "loves to share", "can't hold anything in"]
GOSSIP_INSTRUCTIONS = "Given their personality, how readily does this villager pass news on to whoever they meet?"
KEEN_LEVELS = ["not at all", "a little", "quite a bit", "can't stop"]
KEEN_INSTRUCTIONS = "How much does this villager want to keep talking about this?"
SUSPICION_INSTRUCTIONS = (
    "How strongly does what this villager just heard make them suspect the newcomer is "
    "behind the rumors going around, given their personality?"
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
    suspicion: float
    warn_threshold: float
    # New items that name the newcomer: the newcomer's own whispers and lines that say who started them.
    names_newcomer: list[Match]
    # The villager's own earlier lines that the newcomer's words bring back.
    said: list[Match] = field(default_factory=list)
    # The newcomer's lines that landed this turn and that the villager may still answer.
    answerable: list[Match] = field(default_factory=list)
    # Whether to ask how much of a gossip the villager is, which is asked once per villager.
    ask_gossip: bool = False
    # Whether to ask how keen the villager is to keep a scene going.
    ask_keen: bool = False


@dataclass(frozen=True)
class Decision:
    action: str
    source: Match | None
    target: str | None
    style: str | None
    # How much this turn's news raised suspicion of the newcomer, in [0, 1].
    suspicion_strength: float | None = None
    # How readily the villager passes news on, in [0, 1], when it was asked.
    gossip: float | None = None
    # How keen the villager is to keep talking, in [0, 1], when it was asked.
    keen: float | None = None


def _text(m: Match) -> str:
    return m.data.get("text", "")


def _person(v: Villager, other: Villager) -> str:
    kind = v.relationships.get(other.id)
    return f"{other.name}, {other.occupation}" + (f", their {kind}" if kind else "")


def _suspicion_words(view: TurnView) -> str:
    if view.suspicion <= 0:
        return "none"
    if view.suspicion < view.warn_threshold:
        return "a little"
    if view.suspicion < (1 + view.warn_threshold) / 2:
        return "quite a bit"
    return "convinced"


def _state(view: TurnView) -> dict:
    v = view.villager
    state = {
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
        "suspicion_of_the_newcomer": _suspicion_words(view),
    }
    if view.said:
        state["things_i_said_recently"] = [_text(m) for m in view.said[:MAX_SAID]]
    return state


def _action_criteria(view: TurnView) -> dict:
    out = {}
    for a in available_actions(view):
        note = ACTION_NOT_FOR.get(a)
        out[a] = {"what": ACTIONS[a], "not_for": note} if note else ACTIONS[a]
    return out


def _questions(view: TurnView) -> dict[str, Choice | Score]:
    qs = {"action": Choice(instructions=ACTION_INSTRUCTIONS, criteria=_action_criteria(view))}
    if view.names_newcomer:
        qs["suspicion"] = Score(instructions=SUSPICION_INSTRUCTIONS, criteria=SUSPICION_LEVELS)
    if view.ask_gossip:
        qs["gossip"] = Score(instructions=GOSSIP_INSTRUCTIONS, criteria=GOSSIP_LEVELS)
    if view.ask_keen:
        qs["keen"] = Score(instructions=KEEN_INSTRUCTIONS, criteria=KEEN_LEVELS)
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


def _scaled(resp, key: str, levels: list[str]) -> float | None:
    """A score scaled from its levels to [0, 1], or None when it is missing."""
    score = getattr(getattr(resp, "scores", {}).get(key), "score", None)
    if not isinstance(score, (int, float)):
        return None
    return min(1.0, max(0.0, score / (len(levels) - 1)))


async def decide(jev, view: TurnView) -> Decision:
    """Ask Jev once and keep only the answers the chosen action needs.

    The action is drawn from Jev's probabilities, so a likely but not top
    choice still happens some of the time. Options below MIN_ACTION_PROBABILITY
    are dropped first; if none remain, the most probable one is taken.
    """
    resp = await jev.system_one(state=_state(view), questions=_questions(view))
    pick = {k: c.choice for k, c in resp.choices.items()}
    odds = resp.choices["action"].probabilities
    likely = {a: p for a, p in odds.items() if p >= MIN_ACTION_PROBABILITY}
    if likely:
        action = random.choices(list(likely), weights=list(likely.values()))[0]
    else:
        action = max(odds, key=odds.get)
    log.debug("%s: action odds %s, drew %s", view.villager.id, odds, action)
    strength = _scaled(resp, "suspicion", SUSPICION_LEVELS) if view.names_newcomer else None
    gossip = _scaled(resp, "gossip", GOSSIP_LEVELS) if view.ask_gossip else None
    keen = _scaled(resp, "keen", KEEN_LEVELS) if view.ask_keen else None
    if action == IGNORE:
        return Decision(IGNORE, None, None, None, strength, gossip, keen)
    if action == ANSWER:
        return Decision(ANSWER, view.answerable[-1], None, None, strength, gossip, keen)
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
    # A move keeps a manner in case the villager shares the news where they arrive.
    style = pick.get("style") if action in STYLED or action == MOVE else None
    return Decision(action, source, target, style, strength, gossip, keen)
