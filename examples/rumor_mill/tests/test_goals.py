import random

from examples.rumor_mill.goals import DECK, WORLD_CANDIDATES, WORLD_FRACTION, deal, world_goal


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
