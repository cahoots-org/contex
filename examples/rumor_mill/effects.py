"""Turn a villager's decision into town changes and speech."""

import logging
from collections.abc import Sequence
from typing import Protocol

from examples.rumor_mill.actions import (
    CONFRONT, DENY, FOUND_GROUP, IGNORE, JOIN_GROUP, LEAVE_GROUP, LEAVE_TOWN, MOVE,
    PREACH, RETELL, SPEAK, WARN,
)
from examples.rumor_mill.config import Config
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Group, Utterance, Villager
from examples.rumor_mill.town import Town
from examples.rumor_mill.writer import Writer

log = logging.getLogger(__name__)

DIRECT = (RETELL, DENY, CONFRONT)
# Share of the most one hearing can add that a warning adds to its target.
WARNED_SHARE = 0.5


class Hub(Protocol):
    async def deliver(self, utterance: Utterance, listener_ids: list[str], addressed: bool = False) -> None: ...

    async def publish_group(self, group: Group) -> None: ...

    def suspect(self, vid: str, amount: float, reason: str, uid: str | None = None) -> None: ...


async def act(
    decision: Decision,
    villager: Villager,
    *,
    town: Town,
    writer: Writer,
    hub: Hub,
    lineage: Lineage,
    ids: Ids,
    day: int,
    inspiration: Sequence[str] = (),
    suspicion_hear_max: float = Config.suspicion_hear_max,
) -> None:
    """Apply the decision to the town and deliver any speech it produces."""
    action = decision.action
    vid = villager.id
    place = town.place_of(vid)
    parent = decision.source.data["id"] if decision.source else None
    source_text = decision.source.data.get("text") if decision.source else None

    async def say(
        text: str, listeners: list[str], verb: str = "said", parent: str | None = parent, addressed: bool = False,
    ) -> str:
        uid = ids.next("u")
        lineage.record(uid, parent)
        utterance = Utterance(uid, vid, villager.name, place, text, parent, verb)
        await hub.deliver(utterance, [i for i in listeners if i != vid], addressed=addressed)
        return uid

    def others() -> list[str]:
        return [v.id for v in town.present(place, exclude=vid)]

    if action == IGNORE:
        return
    if action == MOVE:
        town.move(vid, decision.target)
    elif action in DIRECT:
        if decision.target in others():
            audience = town.get(decision.target).name
            text = await writer.say(villager, decision, source_text, audience)
            await say(text, [decision.target])
    elif action == WARN:
        if decision.target in others():
            text = await writer.warn(villager, town.get(decision.target))
            uid = await say(text, [decision.target], "warned", parent=None, addressed=True)
            try:
                hub.suspect(decision.target, WARNED_SHARE * suspicion_hear_max, "was warned about the newcomer", uid)
            except Exception:
                log.exception("suspicion bump for %s failed", decision.target)
    elif action == SPEAK:
        text = await writer.say(villager, decision, source_text, place)
        await say(text, others())
    elif action == FOUND_GROUP:
        name, creed = await writer.describe_group(villager, list(inspiration))
        group = town.found_group(vid, name, creed)
        await hub.publish_group(group)
        await say(f"{villager.name} founded {name}: {creed}", others(), "announced")
    elif action == JOIN_GROUP:
        group = next(g for g in town.visible_groups(vid) if g.id == decision.target)
        town.join(vid, group.id)
        await say(f"{villager.name} joined {group.name}", others(), "announced")
    elif action == LEAVE_GROUP:
        town.leave(vid, decision.target)
    elif action == PREACH:
        group = next(g for g in town.member_groups(vid) if g.id == decision.target)
        text = await writer.say(villager, decision, source_text, group.name)
        await say(text, list(group.members), "preached")
    elif action == LEAVE_TOWN:
        farewell = await writer.say(villager, decision, source_text, "the square")
        town.remove(vid)
        listeners = [v.id for v in town.present("square")]
        await say(f"{villager.name} is leaving town for good: {farewell}", listeners, "announced")
