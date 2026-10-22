import logging
import os
import random
import uuid

import pytest

from examples.rumor_mill.actions import CONFRONT, DENY, RETELL, SPEAK
from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match
from examples.rumor_mill.goals import DECK
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.overhear import Overhear
from examples.rumor_mill.tests.helpers import Always, StubBaselines, StubContex
from examples.rumor_mill.town import NEWCOMER

URL = os.environ.get("RUMOR_MILL_CONTEX_URL")
GOALS = {g.id: g for g in DECK}
WELL = GOALS["well-cursed"]


def line(text="nice weather", place="tavern", action=SPEAK, style="faithful", speaker="v1", verb="said"):
    return Utterance("u-1", speaker, "Bert", place, text, None, verb, action=action, style=style)


async def make(results=None, error=None, rate=0.0, goals=(WELL,), roll=0.99):
    contex = StubContex(results, error)
    overhear = Overhear(contex, StubBaselines(0.6), Config(session="s1", overhear_rate=rate), Always(roll))
    await overhear.publish(list(goals))
    return overhear, contex


@pytest.mark.asyncio
async def test_publishes_belief_goal_evidence_only():
    _, contex = await make(goals=(WELL, GOALS["leave-8"]))
    assert contex.published == [
        ("rumor-mill:s1:goals", "goal:well-cursed", {"id": "well-cursed", "text": "the well is cursed"}),
    ]


@pytest.mark.asyncio
async def test_only_the_players_place():
    overhear, _ = await make(rate=1.0, roll=0.0)
    assert await overhear.keep(line(place="tavern"), "tavern") == "random"
    assert await overhear.keep(line(place="mill"), "tavern") is None
    assert await overhear.keep(line(speaker=NEWCOMER), "tavern") is None
    assert await overhear.keep(line(action="join_group", verb="announced"), "tavern") is None


@pytest.mark.asyncio
async def test_card_hops_are_not_overheard():
    overhear, contex = await make(rate=1.0, roll=0.0)
    assert await overhear.keep(line(action=CONFRONT), "tavern", hop=True) is None
    assert contex.queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "style"), [
    (CONFRONT, "faithful"), (DENY, "delighted"), (SPEAK, "mock"), (SPEAK, "shift_blame"),
])
async def test_heated_kept(action, style):
    overhear, contex = await make()
    assert await overhear.keep(line(action=action, style=style), "tavern") == "heated"
    assert contex.queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["exaggerated", "delighted", "worried", "conspiratorial", "add_detail"])
async def test_lively_kept(style):
    overhear, contex = await make(roll=0.0)
    assert await overhear.keep(line(action=RETELL, style=style), "tavern") == "lively"
    assert contex.queries == []


@pytest.mark.asyncio
async def test_lively_rate():
    overhear = Overhear(StubContex(), StubBaselines(0.6), Config(overhear_rate=1.0), random.Random(7))
    kept = [await overhear.keep(line(style="delighted"), "tavern") for _ in range(4000)]
    assert set(kept) == {"lively", None}
    assert 0.37 < kept.count("lively") / len(kept) < 0.43


@pytest.mark.asyncio
async def test_goal_relevant_kept():
    results = {"the well water tastes of brimstone": [Match("goal:well-cursed", {}, 0.65)],
               "the well is deep": [Match("goal:well-cursed", {}, 0.55)]}
    overhear, contex = await make(results)
    assert await overhear.keep(line("the well water tastes of brimstone"), "tavern") == "goal"
    assert await overhear.keep(line("the well is deep"), "tavern") is None
    assert contex.queries[0] == ("rumor-mill:s1:goals", "the well water tastes of brimstone", {"top_k": 1, "threshold": 0})


@pytest.mark.asyncio
async def test_goal_check_failure_is_not_goal_relevant(caplog):
    overhear, _ = await make(error=RuntimeError("down 0.73"))
    with caplog.at_level(logging.WARNING):
        assert await overhear.keep(line(), "tavern") is None
    assert "u-1" in caplog.text and "0.73" not in caplog.text


@pytest.mark.asyncio
async def test_random_rate():
    contex = StubContex()
    overhear = Overhear(contex, StubBaselines(0.6), Config(overhear_rate=0.125), random.Random(7))
    kept = [await overhear.keep(line(), "tavern") for _ in range(4000)]
    assert set(kept) == {"random", None}
    assert 0.1 < kept.count("random") / len(kept) < 0.15


@pytest.mark.skipif(not URL, reason="RUMOR_MILL_CONTEX_URL not set")
@pytest.mark.asyncio
async def test_live_goal_relevance(tmp_path):
    cfg = Config(contex_url=URL, session=f"rumor-{uuid.uuid4().hex[:8]}", seed_path=str(tmp_path / "seed.json"),
                 overhear_rate=0.0)
    async with ContexClient(URL) as client:
        baselines = Baselines(client, cfg)
        goals = [WELL, GOALS["dragon"]]
        await baselines.load([g.evidence[0] for g in goals])
        overhear = Overhear(client, baselines, cfg, random.Random(1))
        await overhear.publish(goals)
        assert await overhear.keep(line("They say a curse lies on the well and its water is poisoned."), "tavern") == "goal"
        assert await overhear.keep(line("My back has ached since the frost came."), "tavern") is None
