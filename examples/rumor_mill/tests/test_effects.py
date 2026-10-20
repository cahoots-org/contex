import pytest

from examples.rumor_mill.actions import (
    FOUND_GROUP, LEAVE_TOWN, MOVE, PREACH, RETELL, SPEAK,
)
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.effects import act
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Villager
from examples.rumor_mill.town import Town


class StubWriter:
    def __init__(self):
        self.say_calls = []
        self.group_calls = []

    async def say(self, villager, decision, source_text, audience):
        self.say_calls.append((decision.action, source_text, audience))
        return "words"

    async def describe_group(self, villager, inspiration):
        self.group_calls.append(list(inspiration))
        return "Chapel Circle", "be kind"


class RecordingHub:
    def __init__(self):
        self.delivered = []
        self.groups = []

    async def deliver(self, utterance, listener_ids, addressed=False):
        self.delivered.append((utterance, listener_ids))

    async def publish_group(self, group):
        self.groups.append(group)


def make(vid, name):
    return Villager(vid, name, "baker", "calm", "none", [], {})


class Setup:
    def __init__(self):
        self.town = Town()
        for vid, name in (("v1", "Bert"), ("v2", "Ada"), ("v3", "Cy")):
            self.town.add(make(vid, name))
        self.writer = StubWriter()
        self.hub = RecordingHub()
        self.lineage = Lineage()
        self.ids = Ids()

    async def act(self, who, decision, **kw):
        await act(
            decision, self.town.get(who), town=self.town, writer=self.writer,
            hub=self.hub, lineage=self.lineage, ids=self.ids, day=1, **kw,
        )


def source(uid="u-0"):
    return Match("k", {"id": uid, "text": "Bert stole bread"})


@pytest.mark.asyncio
async def test_retell_delivers_to_target_with_parent_lineage():
    s = Setup()
    s.lineage.record("u-0", None, player=True)
    await s.act("v1", Decision(RETELL, source("u-0"), "v2", "faithful"))
    utt, listeners = s.hub.delivered[0]
    assert listeners == ["v2"]
    assert utt.parent == "u-0"
    assert s.lineage.root(utt.id) == s.lineage.root("u-0")
    assert s.writer.say_calls == [(RETELL, "Bert stole bread", "Ada")]


@pytest.mark.asyncio
async def test_retell_to_target_who_moved_away_delivers_nothing():
    s = Setup()
    s.town.move("v2", "tavern")
    await s.act("v1", Decision(RETELL, source(), "v2", None))
    assert s.hub.delivered == []


@pytest.mark.asyncio
async def test_speak_reaches_everyone_present_but_speaker():
    s = Setup()
    await s.act("v1", Decision(SPEAK, None, None, None))
    utt, listeners = s.hub.delivered[0]
    assert sorted(listeners) == ["v2", "v3"]
    assert utt.parent is None
    assert utt.place == "square"


@pytest.mark.asyncio
async def test_speaker_never_receives_own_utterance():
    s = Setup()
    await s.act("v1", Decision(SPEAK, None, None, None))
    s.town.found_group("v1", "G", "c")
    await s.act("v1", Decision(PREACH, None, "g-1", None))
    await s.act("v1", Decision(LEAVE_TOWN, None, None, None))
    assert all("v1" not in ids for _, ids in s.hub.delivered)


@pytest.mark.asyncio
async def test_found_group_adds_group_and_publishes_record():
    s = Setup()
    await s.act("v1", Decision(FOUND_GROUP, None, None, None), inspiration=["a rumor"])
    assert s.writer.group_calls == [["a rumor"]]
    assert s.hub.groups[0].name == "Chapel Circle"
    assert s.town.member_groups("v1")[0].creed == "be kind"
    utt, listeners = s.hub.delivered[0]
    assert sorted(listeners) == ["v2", "v3"]
    assert "Chapel Circle" in utt.text


@pytest.mark.asyncio
async def test_preach_reaches_members_in_other_places():
    s = Setup()
    g = s.town.found_group("v1", "Chapel Circle", "be kind")
    s.town.join("v2", g.id)
    s.town.move("v2", "chapel")
    await s.act("v1", Decision(PREACH, None, g.id, "faithful"))
    utt, listeners = s.hub.delivered[0]
    assert listeners == ["v2"]
    assert s.writer.say_calls == [(PREACH, None, "Chapel Circle")]


@pytest.mark.asyncio
async def test_leave_town_removes_villager_and_announces_in_square():
    s = Setup()
    s.town.move("v3", "tavern")
    await s.act("v1", Decision(LEAVE_TOWN, None, None, None))
    assert s.town.alive_count == 2
    utt, listeners = s.hub.delivered[0]
    assert listeners == ["v2"]
    assert "leaving town" in utt.text and "words" in utt.text
    assert s.writer.say_calls == [(LEAVE_TOWN, None, "the square")]


@pytest.mark.asyncio
async def test_move_changes_place_without_delivery():
    s = Setup()
    await s.act("v1", Decision(MOVE, None, "tavern", None))
    assert s.town.place_of("v1") == "tavern"
    assert s.hub.delivered == []
