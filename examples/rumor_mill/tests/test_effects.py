import pytest

from examples.rumor_mill.actions import (
    ANSWER, FOUND_GROUP, LEAVE_TOWN, MOVE, PREACH, RETELL, SPEAK, WARN,
)
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.effects import act
from examples.rumor_mill.facts import Facts
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.tests.helpers import PID, StubClient
from examples.rumor_mill.town import NEWCOMER, Town
from examples.rumor_mill.writer import Writer


class StubWriter:
    def __init__(self):
        self.say_calls = []
        self.group_calls = []
        self.warn_calls = []
        self.reply_calls = []
        self.names_newcomer = []

    async def say(self, villager, decision, source_text, audience, names_newcomer=False):
        self.say_calls.append((decision.action, source_text, audience))
        self.names_newcomer.append(names_newcomer)
        return "words"

    async def warn(self, villager, target):
        self.warn_calls.append((villager.id, target.id))
        return "Mind the newcomer."

    async def reply(self, villager, newcomer_line, said, heard=()):
        self.reply_calls.append((villager.id, newcomer_line, said, heard))
        return "Aye, I heard that too."

    async def describe_group(self, villager, inspiration):
        self.group_calls.append(list(inspiration))
        return "Chapel Circle", "be kind"


class RecordingHub:
    def __init__(self):
        self.delivered = []
        self.addressed = []
        self.groups = []
        self.suspects = []
        self.replies = []

    async def reply(self, utterance):
        self.replies.append(utterance)

    async def deliver(self, utterance, listener_ids, addressed=False):
        self.delivered.append((utterance, listener_ids))
        self.addressed.append(addressed)

    def suspect(self, vid, player, amount, reason, uid=None):
        self.suspects.append((vid, player, amount, reason, uid))

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
        self.facts = Facts()

    async def act(self, who, decision, **kw):
        await act(
            decision, self.town.get(who), town=self.town, writer=self.writer,
            hub=self.hub, lineage=self.lineage, ids=self.ids, facts=self.facts, **kw,
        )


def source(uid="u-0"):
    return Match("k", {"id": uid, "text": "Bert stole bread"})


@pytest.mark.asyncio
async def test_retell_delivers_to_target_with_parent_lineage():
    s = Setup()
    s.lineage.record("u-0", None, player=PID)
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


@pytest.mark.asyncio
async def test_a_villager_who_moves_may_share_the_news_where_they_arrive():
    s = Setup()
    s.town.move("v2", "tavern")
    await s.act("v1", Decision(MOVE, source(), "tavern", "worried"), share_chance=1.0)
    utt, listeners = s.hub.delivered[0]
    assert listeners == ["v2"] and utt.place == "tavern" and utt.parent == "u-0" and utt.action == RETELL
    assert s.writer.say_calls == [(RETELL, "Bert stole bread", "Ada")]


@pytest.mark.asyncio
async def test_a_move_shares_nothing_without_luck_news_or_company():
    s = Setup()
    s.town.move("v2", "tavern")
    await s.act("v1", Decision(MOVE, source(), "tavern", None), share_chance=0.0)
    await s.act("v1", Decision(MOVE, None, "square", None), share_chance=1.0)
    await s.act("v1", Decision(MOVE, source(), "mill", None), share_chance=1.0)
    assert s.hub.delivered == []


@pytest.mark.asyncio
async def test_warn_is_addressed_to_the_target_only():
    s = Setup()
    await s.act("v1", Decision(WARN, source(), "v2", "faithful"), about=PID)
    assert s.writer.warn_calls == [("v1", "v2")]
    assert s.writer.say_calls == []
    utt, listeners = s.hub.delivered[0]
    assert listeners == ["v2"]
    assert s.hub.addressed == [True]
    assert (utt.verb, utt.parent, utt.text) == ("warned", None, "Mind the newcomer.")
    assert s.lineage.root(utt.id) == utt.id


@pytest.mark.asyncio
async def test_warn_raises_the_targets_suspicion():
    s = Setup()
    await s.act("v1", Decision(WARN, None, "v2", None), about=PID)
    utt, _ = s.hub.delivered[0]
    assert s.hub.suspects == [("v2", PID, 0.5 * Config().suspicion_hear_max, "was warned about the newcomer", utt.id)]

    def broken(*args):
        raise RuntimeError("suspicion down")

    s.hub.suspect = broken
    await s.act("v1", Decision(WARN, None, "v2", None), about=PID)
    assert len(s.hub.delivered) == 2


@pytest.mark.asyncio
async def test_act_records_depth_audience_action_style():
    s = Setup()
    s.facts.record(Utterance("u-0", "v3", "Cy", "square", "Bert stole bread", None, depth=2))
    recorded_first = []

    async def deliver(utterance, listener_ids, addressed=False):
        recorded_first.append(s.facts.get(utterance.id) is utterance)
        s.hub.delivered.append((utterance, listener_ids))

    s.hub.deliver = deliver
    await s.act("v1", Decision(RETELL, source("u-0"), "v2", "faithful"))
    await s.act("v1", Decision(SPEAK, source("u-unknown"), None, "angry"))
    await s.act("v1", Decision(WARN, source("u-0"), "v2", None), about=PID)
    await s.act("v1", Decision(FOUND_GROUP, source("u-0"), None, None))
    got = [(u.depth, u.audience, u.action, u.style, u.names_newcomer) for u, _ in s.hub.delivered]
    assert got == [
        (3, "one", RETELL, "faithful", False),
        (0, "room", SPEAK, "angry", False),
        (0, "one", WARN, None, True),
        (3, "room", FOUND_GROUP, None, False),
    ]
    assert recorded_first == [True] * 4


def test_payload_unchanged():
    u = Utterance("u-1", "v1", "Bert", "square", "hi", None, depth=4, names_newcomer=True,
                  audience="room", action=SPEAK, style="sly")
    assert u.to_data() == {"id": "u-1", "text": "Bert said at the square: hi"}


WHISPER = "A newcomer whispered to you at the square: Bert stole bread"


def whispered(s, uid="w-1"):
    """A player whisper recorded the way the player records one."""
    s.lineage.record(uid, None, player=PID)
    s.facts.record(Utterance(uid, NEWCOMER, "A newcomer", "square", "Bert stole bread", None, "whispered to you"))
    return Match(f"utt:{uid}", {"id": uid, "text": WHISPER})


@pytest.mark.asyncio
async def test_non_faithful_retelling_drops_the_newcomer():
    s = Setup()
    client = StubClient("Bert's a thief, mark me.")
    s.writer = Writer(client, "haiku-test")
    await s.act("v1", Decision(RETELL, whispered(s), "v2", "exaggerated"))
    utterance, _ = s.hub.delivered[0]
    assert utterance.names_newcomer is False
    prompt = client.messages.calls[0]["system"] + client.messages.calls[0]["messages"][0]["content"]
    assert "Bert stole bread" in prompt
    assert "newcomer whispered" not in prompt
    assert "who started" in prompt


@pytest.mark.asyncio
async def test_faithful_retelling_of_a_whisper_names_the_newcomer():
    s = Setup()
    client = StubClient("The newcomer says Bert stole bread.", "Ada heard it from the newcomer.")
    s.writer = Writer(client, "haiku-test")
    await s.act("v1", Decision(RETELL, whispered(s), "v2", "faithful"))
    first, _ = s.hub.delivered[0]
    assert first.names_newcomer is True
    assert WHISPER in client.messages.calls[0]["messages"][0]["content"]
    retold = Match(f"utt:{first.id}", {"id": first.id, "text": "Bert said at the square: words"})
    await s.act("v2", Decision(SPEAK, retold, None, "faithful"))
    assert s.hub.delivered[1][0].names_newcomer is True


@pytest.mark.asyncio
async def test_a_carried_whisper_names_the_newcomer_only_when_the_move_chose_faithful():
    s = Setup()
    s.town.move("v2", "tavern")
    await s.act("v1", Decision(MOVE, whispered(s), "tavern", None), share_chance=1.0)
    s.town.move("v2", "mill")
    await s.act("v1", Decision(MOVE, whispered(s), "mill", "faithful"), share_chance=1.0)
    assert [u.names_newcomer for u, _ in s.hub.delivered] == [False, True]


@pytest.mark.asyncio
async def test_shift_blame_drops_the_newcomer():
    s = Setup()
    await s.act("v1", Decision(RETELL, whispered(s), "v2", "shift_blame"))
    await s.act("v1", Decision(SPEAK, whispered(s, "w-2"), None, "shift_blame"))
    assert [u.names_newcomer for u, _ in s.hub.delivered] == [False, False]
    assert s.writer.names_newcomer == [False, False]


@pytest.mark.asyncio
async def test_answer_emits_a_reply_and_records_it():
    s = Setup()
    s.lineage.record("w-1", None, player=PID)
    whisper = Match("utt:w-1", {"id": "w-1", "text": "A newcomer whispered to you at the square: psst"})
    await s.act(
        "v1", Decision(ANSWER, whisper, None, None),
        said=["Bert said at the square: the well is fine"], heard=["Cy said at the well: the well is dry"],
    )
    assert s.writer.reply_calls == [
        ("v1", whisper.data["text"], ["Bert said at the square: the well is fine"], ["Cy said at the well: the well is dry"]),
    ]
    assert s.hub.delivered == [] and s.writer.say_calls == []
    utt, = s.hub.replies
    assert (utt.speaker, utt.parent, utt.text, utt.verb) == ("v1", "w-1", "Aye, I heard that too.", "answered")
    assert s.lineage.root(utt.id) == "w-1"
    fact = s.facts.get(utt.id)
    assert (fact.action, fact.audience, fact.depth, fact.names_newcomer) == (ANSWER, "one", 0, False)


@pytest.mark.asyncio
async def test_a_warning_is_about_the_player_in_front():
    s = Setup()
    await s.act("v1", Decision(WARN, None, "v2", None), about="p-a")
    assert s.hub.suspects[0][:2] == ("v2", "p-a") and s.hub.delivered[0][0].about == "p-a"


@pytest.mark.asyncio
async def test_a_retold_warning_stays_about_its_player():
    s = Setup()
    await s.act("v1", Decision(WARN, None, "v2", None), about="p-a")
    warning, _ = s.hub.delivered[0]
    await s.act("v2", Decision(RETELL, Match("k", {"id": warning.id, "text": warning.text}), "v3", "faithful"))
    assert s.hub.delivered[1][0].about == "p-a"
