"""Turn a villager's decision into town changes and speech."""

from collections.abc import Sequence
from typing import Protocol

from examples.rumor_mill.actions import (
    CONFRONT, DENY, FOUND_GROUP, IGNORE, JOIN_GROUP, LEAVE_GROUP, LEAVE_TOWN, MOVE,
    PREACH, RETELL, SPEAK,
)
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Group, Utterance, Villager
from examples.rumor_mill.town import Town
from examples.rumor_mill.writer import Writer

DIRECT = (RETELL, DENY, CONFRONT)


class Hub(Protocol):
    async def deliver(self, utterance: Utterance, listener_ids: list[str], addressed: bool = False) -> None: ...

    async def publish_group(self, group: Group) -> None: ...


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
) -> None:
    """Apply the decision to the town and deliver any speech it produces."""
    action = decision.action
    vid = villager.id
    place = town.place_of(vid)
    parent = decision.source.data["id"] if decision.source else None
    source_text = decision.source.data.get("text") if decision.source else None

    async def say(text: str, listeners: list[str], verb: str = "said") -> None:
        uid = ids.next("u")
        lineage.record(uid, parent)
        utterance = Utterance(uid, vid, villager.name, place, text, parent, verb)
        await hub.deliver(utterance, [i for i in listeners if i != vid])

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
