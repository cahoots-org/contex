import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.models import Villager
from examples.rumor_mill.suspicion import Suspicion
from examples.rumor_mill.town import Town


def v(vid, occupation="baker"):
    return Villager(vid, vid.title(), occupation, "kind", "none", [], {})


def make(*villagers, **cfg):
    town = Town()
    for x in villagers:
        town.add(x)
    return Suspicion(Config(**cfg), town), town


def test_bump_clamps_to_one_and_records_evidence():
    s, _ = make(v("a"))
    assert s.bump("a", 0.7, "told them", "u1") == 0.7
    assert s.bump("a", 0.7, "told them") == 1.0
    assert s.score("a") == 1.0
    [e] = s.top_evidence(1)
    assert (e.vid, e.reason) == ("a", "told them")
    assert e.amount == pytest.approx(1.4)


def test_unknown_villager_is_ignored():
    s, _ = make(v("a"))
    assert s.bump("ghost", 0.5, "x") == 0.0
    assert s.score("ghost") == 0.0
    assert s.top_evidence() == []


def test_decay_floors_at_zero():
    s, _ = make(v("a"), v("b"), suspicion_decay_per_day=0.1)
    s.bump("a", 0.25, "x")
    s.bump("b", 0.05, "x")
    s.decay(1)
    assert s.score("a") == pytest.approx(0.15)
    assert s.score("b") == 0.0


def test_level_weights_influential_roles():
    s, _ = make(v("m", "Village Mayor"), v("a"), v("b"), v("c"))
    s.bump("m", 1.0, "x")
    assert s.level() == pytest.approx(3 / 6)


def test_weight_table_is_case_insensitive_substring():
    s, _ = make()
    assert s.weight(v("a", "Head Priest")) == 2.0
    assert s.weight(v("a", "INNKEEPER")) == 2.0
    assert s.weight(v("a", "Constable")) == 2.0
    assert s.weight(v("a", "reeve of the mill")) == 2.0
    assert s.weight(v("a", "baker")) == 1.0


def test_level_of_empty_town_is_zero():
    s, _ = make()
    assert s.level() == 0.0


@pytest.mark.parametrize(
    "score,label", [(0.05, "quiet"), (0.15, "murmurs"), (0.3, "watched"), (0.6, "hunted")]
)
def test_label_bands(score, label):
    s, _ = make(v("a"), run_out_level=0.5)
    s.bump("a", score, "x")
    assert s.label() == label


def test_rate_scales_by_hear_max():
    s, _ = make(v("a"), suspicion_hear_max=0.25)
    assert s.rate("a", 0.5, "heard") == pytest.approx(0.125)
    assert s.score("a") == pytest.approx(0.125)


def test_top_evidence_groups_and_orders():
    s, _ = make(v("a"), v("b"))
    s.bump("a", 0.1, "whispered")
    s.bump("a", 0.1, "whispered")
    s.bump("b", 0.15, "warned")
    s.bump("b", 0.01, "other")
    top = s.top_evidence(2)
    assert [(e.vid, e.reason) for e in top] == [("a", "whispered"), ("b", "warned")]
    assert top[0].amount == pytest.approx(0.2)


def test_over_lists_ids_at_or_above_threshold():
    s, _ = make(v("a"), v("b"))
    s.bump("a", 0.4, "x")
    s.bump("b", 0.39, "x")
    assert s.over(0.4) == ["a"]


def test_departed_villager_leaves_level():
    s, town = make(v("a"), v("b"))
    s.bump("a", 1.0, "x")
    assert s.level() == pytest.approx(0.5)
    town.remove("a")
    assert s.level() == 0.0
    assert s.score("a") == 0.0
    assert s.over(0.1) == []
    assert s.bump("a", 0.5, "x") == 0.0


def test_non_positive_bump_changes_nothing_and_records_no_evidence():
    s, _ = make(v("a"))
    s.bump("a", 0.3, "x")
    assert s.bump("a", -0.5, "y") == 0.3
    assert s.bump("a", 0.0, "z") == 0.3
    assert s.score("a") == 0.3
    assert [e.reason for e in s.top_evidence()] == ["x"]


def test_rate_clamps_strength_to_unit_range():
    s, _ = make(v("a"), v("b"), suspicion_hear_max=0.25)
    assert s.rate("a", -1.0, "x") == 0.0
    assert s.top_evidence() == []
    assert s.rate("b", 5.0, "x") == pytest.approx(0.25)
