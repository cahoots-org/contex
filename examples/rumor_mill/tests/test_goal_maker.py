import asyncio
import random

import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.goal_maker import GoalMaker
from examples.rumor_mill.goals import BANDS
from examples.rumor_mill.lineage import Ids
from examples.rumor_mill.models import Villager
from examples.rumor_mill.overhear import Overhear
from examples.rumor_mill.tests.helpers import Always, StubBaselines, StubContex
from examples.rumor_mill.town import Town

AGNES = Villager("v1", "Agnes Thatch", "weaver", "nosy", "Hob let his pigs into her garden", [], {"v2": "rival"})
HOB = Villager("v2", "Hob Miller", "miller", "gruff", "", [], {"v1": "rival"})
FARMERS = [Villager(f"v{i}", f"Farmer{i} Field", "farmer", "calm", "", [], {}) for i in (3, 4, 5)]

PERSONAL = {
    "kind": "personal", "text": "Convince Agnes that Hob is stealing her geese", "claim": "Hob is stealing Agnes's geese",
    "question": "Does this villager believe Hob is stealing Agnes's geese?", "mark": "Agnes Thatch",
    "subject": "Hob Miller",
}
CIRCLE = {
    "kind": "circle", "claim": "Hob waters his flour", "question": "Does this villager believe Hob waters his flour?",
    "label": "the farmers", "circle": {"by": "occupation", "value": "Farmer"}, "subject": "Hob Miller",
}


class StubWriter:
    def __init__(self, raws, delay=0.0):
        self.raws = raws
        self.delay = delay
        self.samples = []

    async def goals(self, sample, taken):
        self.samples.append((sample, taken))
        await asyncio.sleep(self.delay)
        return [dict(r) for r in self.raws]


class StubBelievability:
    def __init__(self, band=2):
        self.value = band

    async def band(self, claim):
        return self.value


class LoggingBaselines(StubBaselines):
    def __init__(self):
        super().__init__(0.6)
        self.loaded = []

    async def load(self, texts):
        self.loaded.extend(texts)


def make(raws=(PERSONAL, CIRCLE), delay=0.0, band=2, config=None, ids=None):
    town = Town()
    for v in (AGNES, HOB, *FARMERS):
        town.add(v)
    config = config or Config(session="s1")
    baselines = LoggingBaselines()
    contex = StubContex()
    overhear = Overhear(contex, baselines, config, Always(0.99))
    maker = GoalMaker(
        StubWriter(list(raws), delay), StubBelievability(band), baselines, overhear, town, config, random.Random(1),
        ids or Ids(),
    )
    return maker, baselines, overhear, contex


async def hand(maker, pid="p-1", taken=frozenset()):
    return await maker.hand(pid, taken=set(taken), deck_skip=set(), never=set())


@pytest.mark.asyncio
async def test_hand_has_personal_circle_and_deck_goal():
    maker, _, _, _ = make()
    dealt = await hand(maker)
    assert [g.audience_label != "" for g in dealt[:2]] == [True, True]
    assert dealt[0].audience == (AGNES.id,) and dealt[0].subject == HOB.id and dealt[0].owner == "p-1"
    assert dealt[0].text == PERSONAL["text"] and dealt[0].audience_label == "Agnes"
    assert dealt[0].evidence == (PERSONAL["claim"],) and dealt[0].believer_question == PERSONAL["question"]
    assert set(dealt[1].audience) == {f.id for f in FARMERS} and HOB.id not in dealt[1].audience
    assert dealt[1].text == "Get a third of the farmers to believe Hob waters his flour"
    assert dealt[1].target_fraction == BANDS[2][0] and not dealt[1].long_shot
    assert dealt[2].owner is None


@pytest.mark.asyncio
async def test_far_fetched_generated_goals_are_long_shots():
    maker, _, _, _ = make(band=0)
    dealt = await hand(maker)
    assert dealt[0].long_shot and dealt[1].long_shot
    assert dealt[1].text.startswith("Get a fifth of the farmers")


@pytest.mark.asyncio
@pytest.mark.parametrize("circle", [
    {"by": "ties", "value": "Agnes Thatch"}, {"by": "family", "value": "field"}, {"by": "occupation", "value": "FARMER"},
])
async def test_circles_resolve_to_villagers_in_town(circle):
    agnes = Villager("v1", "Agnes Thatch", "weaver", "nosy", "", [], {"v2": "rival", "v3": "friend", "v4": "friend", "v5": "friend", "v9": "friend"})
    maker, _, _, _ = make(raws=(PERSONAL, dict(CIRCLE, circle=circle)))
    maker.town.remove("v1")
    maker.town.add(agnes)
    dealt = await hand(maker)
    assert set(dealt[1].audience) == {f.id for f in FARMERS} and dealt[1].owner == "p-1"


@pytest.mark.asyncio
async def test_invalid_generated_goals_fall_back_to_deck():
    bad = [
        dict(PERSONAL, mark="Nobody Atall"),
        dict(PERSONAL, mark="Hob Miller"),
        dict(CIRCLE, circle={"by": "ties", "value": "Hob Miller"}),
        dict(CIRCLE, claim="the miller waters his flour"),
        {"kind": "circle"},
        {"kind": "personal", "mark": 3},
    ]
    maker, _, _, _ = make(raws=bad)
    dealt = await hand(maker)
    assert len(dealt) == 3 and len({g.id for g in dealt}) == 3
    assert all(g.owner is None for g in dealt)


@pytest.mark.asyncio
async def test_writer_timeout_gives_deck_hand():
    maker, _, _, contex = make(delay=1.0, config=Config(session="s1", goal_writer_timeout_seconds=0.01))
    dealt = await hand(maker)
    assert len(dealt) == 3 and all(g.owner is None for g in dealt)
    assert contex.published == []


@pytest.mark.asyncio
async def test_hand_loads_baselines_before_dealing():
    maker, baselines, overhear, _ = make()
    dealt = await hand(maker)
    assert baselines.loaded == [PERSONAL["claim"], CIRCLE["claim"]]
    assert {f"goal:{g.id}" for g in dealt[:2]} <= overhear._evidence.keys()


@pytest.mark.asyncio
async def test_generated_ids_unique_across_players():
    ids = Ids()
    maker, _, _, _ = make(ids=ids)
    first, second = await hand(maker, "p-1"), await hand(maker, "p-2")
    gen = [g.id for g in first[:2] + second[:2]]
    assert len(set(gen)) == 4 and all(i.startswith("gen-") for i in gen)


@pytest.mark.asyncio
async def test_taken_claim_rejected():
    maker, _, _, _ = make()
    dealt = await hand(maker, taken={"hob is stealing agnes's geese"})
    assert dealt[0].audience_label == "the farmers"
    assert [g.owner for g in dealt] == ["p-1", None, None]


@pytest.mark.asyncio
async def test_sample_prefers_villagers_with_grudges_or_ties():
    maker, _, _, _ = make(config=Config(session="s1", goal_sample_size=2))
    await hand(maker, taken={"the well is cursed"})
    sample, taken = maker.writer.samples[0]
    assert {v.id for v in sample} == {AGNES.id, HOB.id}
    assert taken == ["the well is cursed"]
