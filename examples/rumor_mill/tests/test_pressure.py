import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.pressure import Pressure


def test_pressure_adds_up_and_credits_the_largest_share():
    p = Pressure(Config())
    assert p.add("v1", 0.2, "p-1") == pytest.approx(0.2)
    assert p.add("v1", 0.3, "p-2") == pytest.approx(0.5)
    assert p.add("v1", 0.2, None) == pytest.approx(0.7)
    assert p.level("v1") == pytest.approx(0.7) and p.level("v2") == 0.0
    assert p.culprit("v1") == "p-2" and p.culprit("v2") is None


def test_breaking_words_follow_the_marks():
    p = Pressure(Config())
    assert p.words("v1") == "steady"
    p.add("v1", 0.4, None)
    assert p.words("v1") == "shaken"
    p.add("v1", 0.3, None)
    assert p.words("v1") == "at the end of their rope"


def test_pressure_decays_and_departed_are_dropped():
    p = Pressure(Config(pressure_decay=0.1))
    p.add("v1", 0.3, "p-1")
    p.add("v1", 0.1, "p-2")
    p.add("v2", 0.05, "p-1")
    p.decay()
    assert p.level("v1") == pytest.approx(0.3) and p.level("v2") == 0.0
    assert p._shares["v1"] == pytest.approx({"p-1": 0.225, "p-2": 0.075})
    for _ in range(4):
        p.decay()
    assert p.level("v1") == 0.0 and p.culprit("v1") is None
    p.add("v1", 0.5, "p-1")
    p.drop("v1")
    assert p.level("v1") == 0.0 and p.culprit("v1") is None and "v1" not in p._shares


def test_pressure_from_no_player_has_no_culprit():
    p = Pressure(Config())
    p.add("v1", 1.2, None)
    assert p.culprit("v1") is None
