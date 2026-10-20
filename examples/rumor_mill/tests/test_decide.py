from types import SimpleNamespace

import pytest

from examples.rumor_mill.actions import (
    CONFRONT, DENY, FOUND_GROUP, IGNORE, JOIN_GROUP, LEAVE_GROUP, LEAVE_TOWN, MOVE,
    PREACH, RETELL, SPEAK,
)
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.decide import Decision, TurnView, decide
from examples.rumor_mill.models import Group, Villager


class StubJev:
    def __init__(self, answers):
        self.answers = answers
        self.questions = {}
        self.state = None

    async def system_one(self, state, questions):
        self.state = state
        self.questions = questions
        return SimpleNamespace(
            choices={k: SimpleNamespace(choice=v) for k, v in self.answers.items() if k in questions}
        )


def villager(vid="v1", name="Ada"):
    return Villager(vid, name, "baker", "gossipy", "", [], {})


def match(key, text="x"):
    return Match(key, {"text": text})


def view(**kw):
    base = dict(
        villager=villager(), place="square", present=[villager("v2", "Bo")],
        member_groups=[], visible_groups=[], new=[match("m1")], recent=[],
    )
    return TurnView(**{**base, **kw})


@pytest.mark.asyncio
async def test_alone_in_place_excludes_social_actions_and_target_question():
    jev = StubJev({"action": IGNORE})
    await decide(jev, view(present=[]))
    criteria = jev.questions["action"].criteria
    for a in (RETELL, DENY, CONFRONT, SPEAK):
        assert a not in criteria
    assert "villager_target" not in jev.questions


@pytest.mark.asyncio
async def test_no_groups_omits_group_questions_and_actions():
    jev = StubJev({"action": IGNORE})
    await decide(jev, view())
    assert JOIN_GROUP not in jev.questions["action"].criteria
    assert not {"join_target", "group_target"} & set(jev.questions)


@pytest.mark.asyncio
async def test_single_new_item_skips_source_question_and_is_the_source():
    jev = StubJev({"action": RETELL, "villager_target": "v2", "style": "mock"})
    d = await decide(jev, view())
    assert "source" not in jev.questions
    assert d.source == match("m1")


@pytest.mark.asyncio
async def test_multiple_new_items_ask_source():
    jev = StubJev({"action": RETELL, "source": "m2", "villager_target": "v2", "style": "mock"})
    d = await decide(jev, view(new=[match("m1"), match("m2")]))
    assert set(jev.questions["source"].criteria) == {"m1", "m2"}
    assert d.source.data_key == "m2"


@pytest.mark.asyncio
async def test_target_options_capped_at_255():
    present = [villager(f"v{i}", f"N{i}") for i in range(300)]
    jev = StubJev({"action": IGNORE})
    await decide(jev, view(present=present))
    assert len(jev.questions["villager_target"].criteria) == 255


@pytest.mark.asyncio
async def test_ignore_returns_empty_decision():
    d = await decide(StubJev({"action": IGNORE}), view())
    assert d == Decision(IGNORE, None, None, None)


@pytest.mark.asyncio
async def test_retell_maps_chosen_target_and_style():
    jev = StubJev({"action": RETELL, "villager_target": "v2", "style": "exaggerated"})
    d = await decide(jev, view())
    assert d == Decision(RETELL, match("m1"), "v2", "exaggerated")


@pytest.mark.asyncio
async def test_move_and_join_use_their_own_target_questions():
    g = Group("g-1", "Chapel Circle", "pray", "v9", {"v9"})
    jev = StubJev({"action": MOVE, "place_target": "tavern", "join_target": "g-1"})
    d = await decide(jev, view(visible_groups=[g]))
    assert "square" not in jev.questions["place_target"].criteria
    assert d.target == "tavern"
    jev = StubJev({"action": JOIN_GROUP, "join_target": "g-1"})
    d = await decide(jev, view(visible_groups=[g]))
    assert d.target == "g-1"


@pytest.mark.asyncio
async def test_no_new_items_means_no_source():
    d = await decide(StubJev({"action": SPEAK}), view(new=[]))
    assert d.source is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [DENY, CONFRONT, SPEAK])
async def test_style_kept_for_speech_actions(action):
    jev = StubJev({"action": action, "villager_target": "v2", "style": "soften"})
    assert (await decide(jev, view())).style == "soften"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [MOVE, JOIN_GROUP, LEAVE_TOWN, FOUND_GROUP, IGNORE])
async def test_style_none_for_non_speech(action):
    g = Group("g-1", "Circle", "pray", "v9", {"v9"})
    jev = StubJev({"action": action, "place_target": "well", "join_target": "g-1", "style": "mock"})
    assert (await decide(jev, view(visible_groups=[g]))).style is None


@pytest.mark.asyncio
async def test_leave_group_style_none_and_target_consumed():
    g = Group("g-1", "Circle", "pray", "v1", {"v1"})
    jev = StubJev({"action": LEAVE_GROUP, "group_target": "g-1", "style": "mock"})
    d = await decide(jev, view(member_groups=[g]))
    assert (d.target, d.style) == ("g-1", None)


@pytest.mark.asyncio
async def test_preach_in_empty_room_gets_style_and_group_target():
    g = Group("g-1", "Circle", "pray", "v1", {"v1"})
    jev = StubJev({"action": PREACH, "group_target": "g-1", "style": "exaggerated"})
    d = await decide(jev, view(present=[], member_groups=[g]))
    assert "style" in jev.questions
    assert (d.target, d.style) == ("g-1", "exaggerated")


@pytest.mark.asyncio
async def test_target_descriptions_include_names():
    g = Group("g-1", "Chapel Circle", "pray", "v9", {"v9"})
    jev = StubJev({"action": IGNORE})
    await decide(jev, view(visible_groups=[g], member_groups=[g]))
    assert "Bo" in jev.questions["villager_target"].criteria["v2"]
    for q in ("join_target", "group_target"):
        assert "Chapel Circle" in jev.questions[q].criteria["g-1"]
