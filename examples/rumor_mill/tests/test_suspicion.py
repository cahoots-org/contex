import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.models import Villager
from examples.rumor_mill.suspicion import Suspicion
from examples.rumor_mill.town import Town

P = "p-1"


def v(vid, occupation="baker"):
    return Villager(vid, vid.title(), occupation, "kind", "none", [], {})


def make(*villagers, **cfg):
    town = Town()
    for x in villagers:
        town.add(x)
    return Suspicion(Config(**cfg), town), town


def test_bump_clamps_to_one_and_records_evidence():
    s, _ = make(v("a"))
    assert s.bump("a", P, 0.7, "told them", "u1") == 0.7
    assert s.bump("a", P, 0.7, "told them") == 1.0
    assert s.score("a", P) == 1.0
    [e] = s.top_evidence(P, 1)
    assert (e.vid, e.reason) == ("a", "told them")
    assert e.amount == pytest.approx(1.4)


def test_unknown_villager_is_ignored():
    s, _ = make(v("a"))
    assert s.bump("ghost", P, 0.5, "x") == 0.0
    assert s.score("ghost", P) == 0.0
    assert s.top_evidence(P) == []


def test_decay_floors_at_zero():
    s, _ = make(v("a"), v("b"), suspicion_decay_per_day=0.1)
    s.bump("a", P, 0.25, "x")
    s.bump("b", P, 0.05, "x")
    s.decay(1)
    assert s.score("a", P) == pytest.approx(0.15)
    assert s.score("b", P) == 0.0


def test_level_weights_influential_roles():
    s, _ = make(v("m", "Village Mayor"), v("a"), v("b"), v("c"))
    s.bump("m", P, 1.0, "x")
    assert s.level(P) == pytest.approx(3 / 6)


def test_weight_table_is_case_insensitive_substring():
    s, _ = make()
    assert s.weight(v("a", "Head Priest")) == 2.0
    assert s.weight(v("a", "INNKEEPER")) == 2.0
    assert s.weight(v("a", "Constable")) == 2.0
    assert s.weight(v("a", "reeve of the mill")) == 2.0
    assert s.weight(v("a", "Ale brewer and tavern keeper")) == 2.0
    assert s.weight(v("a", "Village bailiff and law keeper")) == 2.0
    assert s.weight(v("a", "baker")) == 1.0


def test_level_of_empty_town_is_zero():
    s, _ = make()
    assert s.level(P) == 0.0


@pytest.mark.parametrize(
    "score,label", [(0.05, "quiet"), (0.15, "murmurs"), (0.3, "watched"), (0.6, "hunted")]
)
def test_label_bands(score, label):
    s, _ = make(v("a"), run_out_level=0.5)
    s.bump("a", P, score, "x")
    assert s.label(P) == label


def test_rate_scales_by_hear_max():
    s, _ = make(v("a"), suspicion_hear_max=0.25)
    assert s.rate("a", P, 0.5, "heard") == pytest.approx(0.125)
    assert s.score("a", P) == pytest.approx(0.125)


def test_top_evidence_groups_and_orders():
    s, _ = make(v("a"), v("b"))
    s.bump("a", P, 0.1, "whispered")
    s.bump("a", P, 0.1, "whispered")
    s.bump("b", P, 0.15, "warned")
    s.bump("b", P, 0.01, "other")
    top = s.top_evidence(P, 2)
    assert [(e.vid, e.reason) for e in top] == [("a", "whispered"), ("b", "warned")]
    assert top[0].amount == pytest.approx(0.2)


def test_over_lists_ids_at_or_above_threshold():
    s, _ = make(v("a"), v("b"))
    s.bump("a", P, 0.4, "x")
    s.bump("b", P, 0.39, "x")
    assert s.over(0.4, P) == ["a"]


def test_departed_villager_leaves_level():
    s, town = make(v("a"), v("b"))
    s.bump("a", P, 1.0, "x")
    assert s.level(P) == pytest.approx(0.5)
    town.remove("a")
    assert s.level(P) == 0.0
    assert s.score("a", P) == 0.0
    assert s.over(0.1, P) == []
    assert s.bump("a", P, 0.5, "x") == 0.0


def test_non_positive_bump_changes_nothing_and_records_no_evidence():
    s, _ = make(v("a"))
    s.bump("a", P, 0.3, "x")
    assert s.bump("a", P, -0.5, "y") == 0.3
    assert s.bump("a", P, 0.0, "z") == 0.3
    assert s.score("a", P) == 0.3
    assert [e.reason for e in s.top_evidence(P)] == ["x"]


def test_rate_clamps_strength_to_unit_range():
    s, _ = make(v("a"), v("b"), suspicion_hear_max=0.25)
    assert s.rate("a", P, -1.0, "x") == 0.0
    assert s.top_evidence(P) == []
    assert s.rate("b", P, 5.0, "x") == pytest.approx(0.25)


def test_label_without_a_run_out_level_is_hunted():
    s, _ = make(v("a"), run_out_level=0.0)
    assert s.label(P) == "hunted"


def test_suspicion_is_kept_per_player():
    s, _ = make(v("v1"))
    s.bump("v1", "p-a", 0.5, "whispered to them directly")
    assert (s.score("v1", "p-a"), s.score("v1", "p-b")) == (0.5, 0.0)
    assert s.level("p-b") == 0.0 and s.label("p-a") != s.label("p-b")
    assert [e.player for e in s.top_evidence("p-a")] == ["p-a"] and s.reasons("p-b") == []


def test_most_suspected_prefers_the_players_in_front():
    s, _ = make(v("v1"), v("v2"))
    s.bump("v1", "p-a", 0.2, "r")
    s.bump("v1", "p-b", 0.5, "r")
    assert s.most_suspected("v1", {"p-a"}) == "p-a"
    assert s.most_suspected("v1") == "p-b"
    assert s.most_suspected("v2") is None


def test_decay_covers_every_player():
    s, _ = make(v("v1"), suspicion_decay_per_day=0.1)
    s.bump("v1", "p-a", 0.25, "x")
    s.bump("v1", "p-b", 0.05, "x")
    s.decay(1)
    assert s.score("v1", "p-a") == pytest.approx(0.15) and s.score("v1", "p-b") == 0.0
