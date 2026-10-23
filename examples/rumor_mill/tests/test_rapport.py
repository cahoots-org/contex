import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.rapport import Rapport


def test_interest_is_kept_per_rumor():
    r = Rapport(Config())
    assert r.move("v1", "p-1", "w-1", 0.4) == pytest.approx(0.4)
    assert r.move("v1", "p-1", "w-2", -0.35) == pytest.approx(-0.35 + 0.02)
    assert r.interest("v1", "p-1", "w-1") == pytest.approx(0.4)
    r.forget("v1", "p-1", "w-1")
    assert r.interest("v1", "p-1", "w-1") == pytest.approx(0.5 * r.rapport("v1", "p-1"))


def test_rapport_is_per_player_and_carries_to_the_next_rumor():
    r = Rapport(Config())
    r.move("v1", "p-1", "w-1", 0.4)
    assert r.rapport("v1", "p-1") == pytest.approx(0.04)
    assert r.interest("v1", "p-1", "w-2") == pytest.approx(0.02)
    assert r.rapport("v1", "p-2") == 0.0 and r.interest("v1", "p-2", "w-1") == 0.0
    assert r.rapport("v2", "p-1") == 0.0


def test_interest_and_rapport_are_clamped():
    r = Rapport(Config(rapport_share=1.0))
    assert r.move("v1", "p-1", "w-1", 5.0) == 1.0
    assert r.rapport("v1", "p-1") == 1.0
    r.sour("v1", "p-1", -5.0)
    assert r.rapport("v1", "p-1") == -1.0
    assert r.move("v1", "p-1", "w-2", -5.0) == -1.0
