import pytest

from examples.whispers.config import Config
from examples.whispers.rapport import Rapport, interest_words, rapport_words, taken_words


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


def test_interest_and_rapport_words_bands():
    assert [interest_words(x) for x in (1.0, 0.5, 0.49, 0.15, 0.14, 0.0, -0.14, -0.15, -0.44, -0.45, -1.0)] == [
        "eager to hear more", "eager to hear more", "curious", "curious", "lukewarm", "lukewarm", "lukewarm",
        "losing interest", "losing interest", "about out of patience", "about out of patience",
    ]
    assert [rapport_words(x) for x in (1.0, 0.4, 0.39, 0.1, 0.09, 0.0, -0.09, -0.1, -0.39, -0.4, -1.0)] == [
        "fond of the newcomer", "fond of the newcomer", "warm toward the newcomer", "warm toward the newcomer",
        "neutral toward the newcomer", "neutral toward the newcomer", "neutral toward the newcomer",
        "cool toward the newcomer", "cool toward the newcomer", "tired of the newcomer", "tired of the newcomer",
    ]


def test_mood_words_have_no_digits():
    xs = [i / 20 for i in range(-20, 21)]
    assert not any(c.isdigit() for x in xs for c in interest_words(x) + rapport_words(x))


def test_a_greeting_stays_until_spent_and_comes_back_when_forgotten():
    r = Rapport(Config())
    assert r.greets("v1", "p-1", "w-1") and r.greets("v1", "p-1", "w-1")
    r.greeted("v1", "p-1", "w-1")
    assert not r.greets("v1", "p-1", "w-1") and r.greets("v1", "p-1", "w-2")
    r.forget("v1", "p-1", "w-1")
    assert r.greets("v1", "p-1", "w-1")


def test_only_her_own_interest_is_known():
    r = Rapport(Config())
    r.sour("v1", "p-1", 0.5)
    assert not r.known("v1", "p-1", "w-1")
    r.move("v1", "p-1", "w-1", 0.1)
    assert r.known("v1", "p-1", "w-1") and not r.known("v2", "p-1", "w-1")


def test_taken_words_bands():
    xs = (1.0, 0.5, 0.49, 0.15, 0.14, 0.0, -0.29, -0.3, -1.0)
    assert [taken_words(x) for x in xs] == [
        "eager", "eager", "interested", "interested", "indifferent", "indifferent", "indifferent", "put off", "put off",
    ]
