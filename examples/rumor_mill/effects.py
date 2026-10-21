"""Turn a villager's decision into town changes and speech."""

import logging
import random
from collections.abc import Sequence
from typing import Protocol

from examples.rumor_mill.actions import (
    ANSWER, CONFRONT, DENY, FOUND_GROUP, IGNORE, JOIN_GROUP, LEAVE_GROUP, LEAVE_TOWN, MOVE,
    PREACH, RETELL, SPEAK, WARN,
)
from examples.rumor_mill.config import Config
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.facts import Facts
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Group, Utterance, Villager
from examples.rumor_mill.town import Town
from examples.rumor_mill.writer import Writer

log = logging.getLogger(__name__)

DIRECT = (RETELL, DENY, CONFRONT)
ROOM = "room"
ONE = "one"
# Share of the most one hearing can add that a warning adds to its target.
WARNED_SHARE = 0.5


class Hub(Protocol):
    async def deliver(self, utterance: Utterance, listener_ids: list[str], addressed: bool = False) -> None: ...

    async def publish_group(self, group: Group) -> None: ...

    def suspect(self, vid: str, amount: float, reason: str, uid: str | None = None) -> None: ...

    async def reply(self, utterance: Utterance) -> None: ...


async def act(
    decision: Decision,
    villager: Villager,
    *,
    town: Town,
    writer: Writer,
    hub: Hub,
    lineage: Lineage,
    ids: Ids,
    facts: Facts,
    inspiration: Sequence[str] = (),
    said: Sequence[str] = (),
    suspicion_hear_max: float = Config.suspicion_hear_max,
    share_chance: float = 0.0,
) -> None:
    """Apply the decision to the town and deliver any speech it produces.

    A villager who moves shares the news with someone where they arrive with share_chance.
    """
    action = decision.action
    vid = villager.id
    place = town.place_of(vid)
    source_id = decision.source.data["id"] if decision.source else None
    source_text = decision.source.data.get("text") if decision.source else None
    heard_from = facts.get(source_id) if source_id else None
    from_newcomer = bool(source_id) and (
        lineage.is_whisper(source_id) or (heard_from is not None and heard_from.names_newcomer)
    )
    # Only a faithful retelling of the whisper, or of a line naming the newcomer, keeps the newcomer named.
    names = from_newcomer and decision.style == "faithful"

    async def say(
        text: str, listeners: list[str], audience: str, verb: str = "said", addressed: bool = False,
        root: bool = False, names_newcomer: bool = False,
    ) -> str:
        """Record and deliver an utterance descended from the decision's source, or starting a new line if root."""
        parent = None if root else source_id
        uid = ids.next("u")
        lineage.record(uid, parent)
        depth = heard_from.depth + 1 if heard_from and parent else 0
        utterance = Utterance(
            uid, vid, villager.name, place, text, parent, verb,
            depth=depth, names_newcomer=names_newcomer, audience=audience, action=action, style=decision.style,
        )
        facts.record(utterance)
        await hub.deliver(utterance, [i for i in listeners if i != vid], addressed=addressed)
        return uid

    def others() -> list[str]:
        return [v.id for v in town.present(place, exclude=vid)]

    if action == IGNORE:
        return
    if action == MOVE:
        town.move(vid, decision.target)
        arrived = [v.id for v in town.present(decision.target, exclude=vid)]
        if decision.source and arrived and random.random() < share_chance:
            shared = Decision(RETELL, decision.source, random.choice(arrived), decision.style or "faithful")
            await act(
                shared, villager, town=town, writer=writer, hub=hub, lineage=lineage, ids=ids, facts=facts,
                inspiration=inspiration, said=said, suspicion_hear_max=suspicion_hear_max,
            )
    elif action in DIRECT:
        if decision.target in others():
            audience = town.get(decision.target).name
            text = await writer.say(villager, decision, source_text, audience, names)
            await say(text, [decision.target], ONE, names_newcomer=names)
    elif action == WARN:
        if decision.target in others():
            text = await writer.warn(villager, town.get(decision.target))
            uid = await say(text, [decision.target], ONE, "warned", addressed=True, root=True, names_newcomer=True)
            try:
                hub.suspect(decision.target, WARNED_SHARE * suspicion_hear_max, "was warned about the newcomer", uid)
            except Exception:
                log.exception("suspicion bump for %s failed", decision.target)
    elif action == ANSWER:
        text = await writer.reply(villager, source_text, list(said))
        uid = ids.next("u")
        lineage.record(uid, source_id)
        utterance = Utterance(uid, vid, villager.name, place, text, source_id, "answered", audience=ONE, action=action)
        facts.record(utterance)
        await hub.reply(utterance)
    elif action == SPEAK:
        text = await writer.say(villager, decision, source_text, place, names)
        await say(text, others(), ROOM, names_newcomer=names)
    elif action == FOUND_GROUP:
        name, creed = await writer.describe_group(villager, list(inspiration))
        group = town.found_group(vid, name, creed)
        await hub.publish_group(group)
        await say(f"{villager.name} founded {name}: {creed}", others(), ROOM, "announced")
    elif action == JOIN_GROUP:
        group = next(g for g in town.visible_groups(vid) if g.id == decision.target)
        town.join(vid, group.id)
        await say(f"{villager.name} joined {group.name}", others(), ROOM, "announced")
    elif action == LEAVE_GROUP:
        town.leave(vid, decision.target)
    elif action == PREACH:
        group = next(g for g in town.member_groups(vid) if g.id == decision.target)
        text = await writer.say(villager, decision, source_text, group.name, names)
        await say(text, list(group.members), ROOM, "preached", names_newcomer=names)
    elif action == LEAVE_TOWN:
        farewell = await writer.say(villager, decision, source_text, "the square")
        town.remove(vid)
        listeners = [v.id for v in town.present("square")]
        await say(f"{villager.name} is leaving town for good: {farewell}", listeners, ROOM, "announced")
