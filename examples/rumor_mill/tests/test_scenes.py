import random

import pytest

from examples.rumor_mill.actions import CONFRONT, MOVE, RETELL, SPEAK
from examples.rumor_mill.config import Config
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.scenes import MAX_PEOPLE, Scenes, heat
from examples.rumor_mill.tests.helpers import Always, make
from examples.rumor_mill.town import PLAYER_ID, Town


def town_with(*ids, place="tavern"):
    town = Town()
    for vid in ids:
        town.add(make(vid), place)
    return town


def opening(action=SPEAK, style="faithful", speaker="v1", place="tavern", uid="u-1"):
    return Utterance(uid, speaker, speaker.upper(), place, "news", None, action=action, style=style)


def scenes_for(*ids, rng=None, **config):
    town = town_with(*ids)
    return Scenes(Config(**config), town, rng or random.Random(1)), town


def test_heat_ranks_actions_and_styles():
    assert heat(CONFRONT, "faithful") == "heated"
    assert heat(SPEAK, "mock") == "heated"
    assert heat(SPEAK, "delighted") == "lively"
    assert heat(None, None) == "plain"


@pytest.mark.parametrize(
    ("action", "style", "momentum"), [(CONFRONT, "faithful", 0.6), (RETELL, "delighted", 0.45), (SPEAK, "faithful", 0.3)],
)
def test_opening_heat_sets_the_starting_momentum(action, style, momentum):
    scenes, _ = scenes_for("v1", "v2")
    scene = scenes.start(opening(action, style), "v2", "w-1", "w-1")
    assert scene.momentum == momentum
    assert scene.lines == ["u-1"] and scene.participants == {"v1", "v2"} and scene.id == "s-1"
    assert scenes.of("v2") is scene and scenes.holding("u-1") is scene


def test_no_more_than_max_scenes_run():
    scenes, _ = scenes_for("v1", "v2", max_scenes=1)
    assert not scenes.full()
    scene = scenes.start(opening(), "v2", "w-1", None)
    assert scenes.full()
    assert scenes.start(opening(speaker="v2"), "v1", "w-1", None) is None
    scenes.end(scene, "cooled")
    assert scene.ended == "cooled" and scenes.of("v1") is None and not scenes.full()
    assert scenes.start(opening(), "v2", "w-1", None).id == "s-2"


def test_a_keen_heated_line_raises_momentum_and_a_dull_one_cools_it():
    scenes, _ = scenes_for("v1", "v2")
    scene = scenes.start(opening(), "v2", "w-1", None)
    assert scenes.turned(scene, "v2", SPEAK, "mock", 1.0, False) is None
    assert scene.momentum == 1.0
    assert scenes.turned(scene, "v1", SPEAK, "faithful", 0.0, False) is None
    assert scene.momentum == pytest.approx(0.6)
    assert scenes.turned(scene, "v2", SPEAK, "faithful", 0.0, False) == "cooled"
    assert scene.momentum == pytest.approx(0.2)
    assert scene.people == {"v1", "v2"}


def test_a_join_bonus_and_missing_keen_count():
    scenes, _ = scenes_for("v1", "v2")
    scene = scenes.start(opening(), "v2", "w-1", None)
    scenes.turned(scene, "v2", SPEAK, "faithful", None, True)
    assert scene.momentum == pytest.approx(0.3 + 0.1 - 0.4)


def test_a_non_speech_turn_leaves_and_two_becomes_alone():
    scenes, _ = scenes_for("v1", "v2", "v3")
    scene = scenes.start(opening(), "v2", "w-1", None)
    scenes.join(scene, "v3")
    assert scenes.turned(scene, "v3", MOVE, None, None, False) == "cooled"
    assert scene.participants == {"v1", "v2"} and "v3" not in scene.people
    assert scene.momentum == 0.0
    assert scenes.turned(scene, "v2", None, None, None, False) == "alone"


def test_a_participant_who_walked_off_is_not_at_the_place():
    scenes, town = scenes_for("v1", "v2")
    scene = scenes.start(opening(), "v2", "w-1", None)
    town.move("v2", "well")
    assert scenes.turned(scene, "v1", SPEAK, "mock", 1.0, False) == "alone"


def test_the_line_cap_ends_it():
    scenes, _ = scenes_for("v1", "v2", scene_max_lines=3)
    scene = scenes.start(opening(), "v2", "w-1", None)
    scenes.said(scene, "u-2")
    assert scenes.turned(scene, "v2", SPEAK, "mock", 1.0, False) is None
    scenes.said(scene, "u-3")
    assert scenes.turned(scene, "v1", SPEAK, "mock", 1.0, False) == "long"


def test_join_stops_at_six():
    ids = [f"v{i}" for i in range(1, 9)]
    scenes, _ = scenes_for(*ids)
    scene = scenes.start(opening(), "v2", "w-1", None)
    joined = [scenes.join(scene, vid) for vid in ids[2:]]
    assert joined == [True] * (MAX_PEOPLE - 2) + [False] * 2
    assert len(scene.participants) == MAX_PEOPLE


def test_addressed_draws_bystanders_at_momentum_times_join_rate():
    scenes, town = scenes_for("v1", "v2", "v3", "v4", rng=Always(0.0))
    town.add(make("v5"), "well")
    scene = scenes.start(opening(), "v2", "w-1", None)
    assert scenes.addressed(scene, "v1") == ["v2", "v3", "v4"]
    scenes.rng = Always(0.99)
    assert scenes.addressed(scene, "v1") == ["v2"]
    scenes.rng = Always(0.3 * 0.3 - 0.001)
    assert scenes.addressed(scene, "v1") == ["v2", "v3", "v4"]
    scenes.rng = Always(0.3 * 0.3 + 0.001)
    assert scenes.addressed(scene, "v1") == ["v2"]


def test_addressed_never_draws_the_player_and_stops_at_six():
    ids = [f"v{i}" for i in range(1, 10)]
    scenes, town = scenes_for(*ids, rng=Always(0.0))
    town.add(make(PLAYER_ID), "tavern")
    scene = scenes.start(opening(), "v2", "w-1", None)
    heard = scenes.addressed(scene, "v1")
    assert PLAYER_ID not in heard and "v1" not in heard
    assert len(heard) + 1 == MAX_PEOPLE


def test_speaking_needs_speech_at_the_scene_place():
    scenes, _ = scenes_for("v1", "v2", "v3")
    scene = scenes.start(opening(), "v2", "w-1", None)
    assert scenes.speaking(opening(RETELL, speaker="v2")) is scene
    assert scenes.speaking(opening(MOVE, speaker="v2")) is None
    assert scenes.speaking(opening(speaker="v2", place="well")) is None
    assert scenes.speaking(opening(speaker="v3")) is None
