import logging
import random
from types import SimpleNamespace

import pytest

from examples.rumor_mill.actions import (
    ANSWER, CONFRONT, DENY, FOUND_GROUP, IGNORE, JOIN_GROUP, LEAVE_GROUP, LEAVE_TOWN, MOVE,
    PREACH, RETELL, SPEAK, WARN,
)
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.decide import GOSSIP_LEVELS, SUSPICION_LEVELS, Decision, TurnView, decide
from examples.rumor_mill.models import Group, Villager


class StubJev:
    def __init__(self, answers, scores=None):
        self.answers = answers
        self.scores = scores
        self.questions = {}
        self.state = None

    async def system_one(self, state, questions):
        self.state = state
        self.questions = questions
        choices = {
            k: SimpleNamespace(choice=v, probabilities={v: 1.0})
            for k, v in self.answers.items() if k in questions
        }
        if self.scores is None:
            return SimpleNamespace(choices=choices)
        scores = {k: SimpleNamespace(score=v, confidence=0.9) for k, v in self.scores.items() if k in questions}
        return SimpleNamespace(choices=choices, scores=scores)


def villager(vid="v1", name="Ada"):
    return Villager(vid, name, "baker", "gossipy", "", [], {})


def match(key, text="x"):
    return Match(key, {"text": text})


def view(**kw):
    base = dict(
        villager=villager(), place="square", present=[villager("v2", "Bo")],
        member_groups=[], visible_groups=[], new=[match("m1")], recent=[],
        suspicion=0.0, warn_threshold=0.4, names_newcomer=[],
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
@pytest.mark.parametrize("action", [JOIN_GROUP, LEAVE_TOWN, FOUND_GROUP, IGNORE])
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


@pytest.mark.asyncio
async def test_relationships_are_named_for_people_present():
    ada = Villager("v1", "Ada", "baker", "gossipy", "", [], {"v2": "rival", "v9": "spouse"})
    jev = StubJev({"action": IGNORE})
    await decide(jev, view(villager=ada))
    assert jev.state["relationships_here"] == {"Bo": "rival"}
    assert jev.questions["villager_target"].criteria["v2"] == "Bo, baker, their rival"


@pytest.mark.asyncio
async def test_action_is_drawn_from_jev_probabilities():
    jev = StubJev({"action": SPEAK})
    real = jev.system_one

    async def system_one(state, questions):
        resp = await real(state, questions)
        resp.choices["action"].probabilities = {SPEAK: 0.0, FOUND_GROUP: 1.0}
        return resp

    jev.system_one = system_one
    decision = await decide(jev, view())
    assert decision.action == FOUND_GROUP


def with_odds(odds):
    jev = StubJev({"action": max(odds, key=odds.get)})
    real = jev.system_one

    async def system_one(state, questions):
        resp = await real(state, questions)
        resp.choices["action"].probabilities = odds
        return resp

    jev.system_one = system_one
    return jev


@pytest.mark.asyncio
async def test_options_below_the_floor_are_never_drawn():
    random.seed(7)
    jev = with_odds({SPEAK: 0.57, RETELL: 0.40, FOUND_GROUP: 0.03})
    drawn = {(await decide(jev, view())).action for _ in range(500)}
    assert drawn == {SPEAK, RETELL}


@pytest.mark.asyncio
async def test_argmax_chosen_when_every_option_is_below_the_floor():
    jev = with_odds({SPEAK: 0.09, RETELL: 0.05, FOUND_GROUP: 0.04})
    assert {(await decide(jev, view())).action for _ in range(50)} == {SPEAK}


@pytest.mark.asyncio
async def test_odds_and_drawn_action_are_logged_at_debug(caplog):
    jev = with_odds({SPEAK: 1.0})
    with caplog.at_level(logging.DEBUG):
        await decide(jev, view())
    assert "v1" in caplog.text and SPEAK in caplog.text


@pytest.mark.asyncio
async def test_score_question_only_when_a_line_names_the_newcomer():
    jev = StubJev({"action": IGNORE}, scores={"suspicion": 2.0})
    d = await decide(jev, view())
    assert "suspicion" not in jev.questions
    assert d.suspicion_strength is None
    d = await decide(jev, view(names_newcomer=[match("m1")]))
    question = jev.questions["suspicion"]
    assert list(question.criteria) == SUSPICION_LEVELS == ["not at all", "a little", "quite a bit", "convinced"]
    assert all(s in question.instructions for s in ("newcomer", "personality", "rumors going around"))
    assert "hurtful" not in question.instructions
    assert "sensational" not in question.instructions
    assert d.action == IGNORE
    assert d.suspicion_strength == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_missing_suspicion_answer_leaves_strength_none():
    rooted = view(names_newcomer=[match("m1")])
    assert (await decide(StubJev({"action": IGNORE}), rooted)).suspicion_strength is None
    assert (await decide(StubJev({"action": IGNORE}, scores={}), rooted)).suspicion_strength is None
    assert (await decide(StubJev({"action": IGNORE}, scores={"suspicion": "high"}), rooted)).suspicion_strength is None
    assert (await decide(StubJev({"action": IGNORE}, scores={"suspicion": 9.0}), rooted)).suspicion_strength == 1.0


@pytest.mark.asyncio
async def test_warn_offered_only_above_threshold_and_with_someone_present():
    jev = StubJev({"action": IGNORE})
    await decide(jev, view(suspicion=0.39))
    assert WARN not in jev.questions["action"].criteria
    await decide(jev, view(suspicion=0.4))
    assert WARN in jev.questions["action"].criteria
    await decide(jev, view(suspicion=0.9, present=[]))
    assert WARN not in jev.questions["action"].criteria


@pytest.mark.asyncio
async def test_state_shows_suspicion_in_words_not_numbers():
    jev = StubJev({"action": IGNORE})
    for score, words in ((0.0, "none"), (0.2, "a little"), (0.5, "quite a bit"), (0.95, "convinced")):
        await decide(jev, view(suspicion=score))
        assert jev.state["suspicion_of_the_newcomer"] == words
        assert str(score) not in str(jev.state)


@pytest.mark.asyncio
async def test_warn_maps_target_and_style():
    jev = StubJev({"action": WARN, "villager_target": "v2", "style": "exaggerated"})
    d = await decide(jev, view(suspicion=0.5))
    assert d == Decision(WARN, match("m1"), "v2", "exaggerated")


@pytest.mark.asyncio
async def test_said_lines_reach_the_jev_state_at_most_three():
    jev = StubJev({"action": IGNORE})
    await decide(jev, view(said=[match(f"s{i}", f"line {i}") for i in range(5)]))
    assert jev.state["things_i_said_recently"] == ["line 0", "line 1", "line 2"]


@pytest.mark.asyncio
async def test_no_said_lines_leave_the_key_out_of_the_state():
    jev = StubJev({"action": IGNORE})
    await decide(jev, view())
    assert "things_i_said_recently" not in jev.state


@pytest.mark.asyncio
async def test_answer_offered_only_after_a_landed_newcomer_line():
    jev = StubJev({"action": IGNORE})
    await decide(jev, view())
    assert ANSWER not in jev.questions["action"].criteria
    line = match("utt:w-1", "The newcomer whispered to you at the square: psst")
    jev = StubJev({"action": ANSWER, "source": "m1", "style": "mock", "villager_target": "v2"})
    decision = await decide(jev, view(new=[match("m1"), line], answerable=[line]))
    assert ANSWER in jev.questions["action"].criteria
    assert decision == Decision(ANSWER, line, None, None)


@pytest.mark.asyncio
async def test_warm_styles_are_offered_and_kept():
    jev = StubJev({"action": SPEAK, "style": "conspiratorial"})
    d = await decide(jev, view())
    assert {"delighted", "worried", "conspiratorial"} <= set(jev.questions["style"].criteria)
    assert d.style == "conspiratorial"


@pytest.mark.asyncio
async def test_gossip_is_asked_only_when_requested_and_scaled():
    jev = StubJev({"action": IGNORE}, scores={"gossip": 3.0})
    assert (await decide(jev, view())).gossip is None and "gossip" not in jev.questions
    d = await decide(jev, view(ask_gossip=True))
    assert list(jev.questions["gossip"].criteria) == GOSSIP_LEVELS
    assert d.gossip == 1.0


@pytest.mark.asyncio
async def test_a_move_keeps_its_manner_for_sharing_on_arrival():
    jev = StubJev({"action": MOVE, "place_target": "tavern", "style": "worried"})
    d = await decide(jev, view())
    assert (d.action, d.target, d.style) == (MOVE, "tavern", "worried")
