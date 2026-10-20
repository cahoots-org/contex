import random

from examples.rumor_mill.goals import DECK, deal


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
