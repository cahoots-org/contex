import json
import logging

import pytest

from examples.whispers.config import Config
from examples.whispers.contex_client import Match
from examples.whispers.hints import HINT_TOP_K, Hints
from examples.whispers.tests.helpers import StubContex

DRAFT = "the vines are failing"


def make(**kwargs):
    contex = StubContex(**kwargs)
    config = Config()
    return Hints(contex, config), contex, config


@pytest.mark.asyncio
async def test_add_publishes_to_the_players_own_project():
    hints, contex, config = make()
    await hints.add("p-1", "v1", "The vines worry me.")
    s = config.session
    assert contex.published[0] == (f"whispers:{s}:hints:p-1", "hint:1", {"id": "v1", "text": "The vines worry me."})
    assert hints.project("p-2") == f"whispers:{s}:hints:p-2"


@pytest.mark.asyncio
async def test_matching_returns_villagers_whose_hints_match_best_first_once():
    hints, contex, config = make()
    contex.results[DRAFT] = [Match("hint:3", {"id": "v2"}, 0.9), Match("hint:1", {"id": "v1"}, 0.88),
                             Match("hint:2", {"id": "v2"}, 0.85)]
    await hints.add("p-1", "v1", "The vines worry me.")
    assert await hints.matching("p-1", DRAFT) == ["v2", "v1"]
    assert contex.queries == [(hints.project("p-1"), DRAFT, {"top_k": HINT_TOP_K, "threshold": Config().draft_floor})]


@pytest.mark.asyncio
async def test_no_query_before_the_player_has_a_hint():
    hints, contex, _ = make()
    await hints.add("p-1", "v1", "The vines worry me.")
    assert await hints.matching("p-2", DRAFT) == []
    assert contex.queries == []


@pytest.mark.asyncio
async def test_a_failed_query_matches_no_one(caplog):
    hints, _, _ = make(error=RuntimeError("down"))
    await hints.add("p-1", "v1", "The vines worry me.")
    with caplog.at_level(logging.DEBUG):
        assert await hints.matching("p-1", DRAFT) == []
    assert DRAFT not in caplog.text


@pytest.mark.asyncio
async def test_state_round_trips_as_json_and_the_key_can_be_raised():
    hints, contex, config = make()
    await hints.add("p-1", "v1", "The vines worry me.")
    await hints.add("p-2", "v1", "Hm.")
    fresh = Hints(contex, config)
    fresh.restore(json.loads(json.dumps(hints.to_state())))
    assert fresh._hinted == {"p-1", "p-2"}
    await fresh.add("p-1", "v2", "Again.")
    assert contex.published[-1][1] == "hint:3"
    fresh.ensure_past(10)
    fresh.ensure_past(5)
    await fresh.add("p-1", "v2", "Later.")
    assert contex.published[-1][1] == "hint:11"
