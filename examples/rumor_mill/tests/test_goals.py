import random

import pytest

from examples.rumor_mill import goals
from examples.rumor_mill.goals import (
    DECK, WORLD_CANDIDATES, WORLD_FRACTION, Goal, Trophy, claim, deal, rebar, trophy_line, world_goal,
)


def test_deck_is_well_formed():
    assert len({g.id for g in DECK}) == len(DECK)
    assert len(DECK) >= 15
    assert sum(g.kind == "state" for g in DECK) >= 5
    for g in DECK:
        if g.kind == "belief":
            assert g.evidence and g.believer_question and g.target_fraction > 0
        else:
            assert g.state_check is not None


def test_deal_returns_n_distinct_goals():
    goals = deal(random.Random(1), 3)
    assert len(goals) == 3
    assert len({g.id for g in goals}) == 3


def test_world_goal_raises_the_bar_on_a_belief_claim():
    g = world_goal(next(g for g in DECK if g.id == "mayors-cat"))
    assert (g.id, g.target_fraction) == ("world-mayors-cat", WORLD_FRACTION)
    assert g.text == "Two thirds of the village believes the mayor's cat is a spy"
    assert all(c.kind == "belief" and c.creed_question is None for c in WORLD_CANDIDATES)


def test_deal_skips_held_claims():
    skip = {g.id for g in DECK[:10]}
    assert not {g.id for g in deal(random.Random(1), skip=skip)} & skip


def test_deal_ignores_the_skip_list_when_it_leaves_too_few():
    hand = deal(random.Random(1), skip={g.id for g in DECK[2:]})
    assert len({g.id for g in hand}) == 3


def test_rebar_sets_fraction_text_and_long_shot():
    g = next(g for g in DECK if g.id == "moon-omen")
    low = rebar(g, 0)
    assert low.target_fraction == 1 / 5 and low.long_shot
    assert low.text == "A fifth of the village believes the moon is an omen of disaster"
    high = rebar(g, 3)
    assert high.target_fraction == 1 / 2 and not high.long_shot and high.text.startswith("Half of the village believes")


def test_rebar_leaves_creed_and_state_goals():
    for g in DECK:
        if g.creed_question or g.kind == "state":
            assert rebar(g, 0) == g


def test_claim_prefers_claim_text():
    assert claim(Goal("x", "Convince Agnes that Hob steals geese", "belief", claim_text="Hob steals geese")) == "Hob steals geese"


def test_deck_claim_text_matches_parsed_claim():
    for g in DECK:
        if g.kind == "belief" and g.creed_question is None:
            assert g.claim_text and g.claim_text in g.text


@pytest.mark.asyncio
async def test_rebar_deck_rewrites_deck_and_world_candidates(monkeypatch):
    class Low:
        async def band(self, claim):
            return 0

    monkeypatch.setattr(goals, "DECK", goals.DECK)
    monkeypatch.setattr(goals, "WORLD_CANDIDATES", goals.WORLD_CANDIDATES)
    await goals.rebar_deck(Low())
    village = [g for g in goals.DECK if g.kind == "belief" and g.creed_question is None]
    assert village and all(g.long_shot for g in village)
    assert all(g.long_shot for g in goals.WORLD_CANDIDATES)


def test_trophy_line_never_knew():
    line = trophy_line(Trophy("personal", ("x",), 1), "Hob steals geese", unseen="Hob")
    assert line.endswith(" And Hob never knew.")
    assert "never knew" not in trophy_line(Trophy("personal", ("x",), 1), "Hob steals geese")


def test_trophy_json_carries_long_shot():
    assert Trophy("personal", ("x",), 1).to_json()["long_shot"] is False
    assert Trophy("world", ("x",), 1, long_shot=True).to_json()["long_shot"] is True
