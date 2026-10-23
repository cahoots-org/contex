import asyncio
import json
import logging
import random

import pytest

from examples.whispers import population as population_mod
from examples.whispers.config import Config
from examples.whispers.lineage import Ids, Lineage
from examples.whispers.models import Villager
from examples.whispers.players import Players
from examples.whispers.population import Replenisher
from examples.whispers.tests.helpers import stub_player
from examples.whispers.town import GATE, START_PLACE, Town

TIMEOUT = 2.0
SETTLE = 0.15


def make(vid, occupation="baker"):
    return Villager(vid, vid.upper(), occupation, "calm", "none", ["bread"], {})


class StubBaselines:
    def __init__(self, engine):
        self.engine = engine
        self.loaded = []

    async def load(self, texts):
        self.loaded.append((list(texts), len(self.engine.added)))


class StubEngine:
    def __init__(self, town):
        self.town = town
        self.baselines = StubBaselines(self)
        self.lineage = Lineage()
        self.ids = Ids()
        self.departures: asyncio.Queue[str] = asyncio.Queue()
        self.added: list[tuple[str, str]] = []
        self.delivered = []
        self.fail_add = False
        self.routine = None
        self.players = Players(None)
        self.players.add(stub_player())

    async def add_villager(self, v, place):
        if self.routine:
            assert v.id in self.routine.added
        if self.fail_add:
            raise RuntimeError("contex down")
        self.town.add(v, place)
        self.added.append((v.id, place))

    async def deliver(self, utterance, listener_ids):
        self.delivered.append((utterance, listener_ids))

    def leave(self, vid):
        self.town.remove(vid)
        self.departures.put_nowait(vid)


@pytest.fixture
def newcomers(monkeypatch):
    seen = []

    async def fake(writer, existing, rng, departed=()):
        seen.append([v.id for v in existing] + [v.id for v in departed])
        return make(f"n{len(seen)}", "cobbler")

    monkeypatch.setattr(population_mod, "generate_newcomer", fake)
    return seen


async def start(count, target):
    town = Town()
    for i in range(count):
        town.add(make(f"v{i}"), START_PLACE)
    engine = StubEngine(town)
    rep = Replenisher(engine, town, None, Config(population=target, arrival_delay_seconds=0.05), random.Random(1))
    task = asyncio.create_task(rep.run())
    return engine, town, task


async def wait_for(predicate, timeout=TIMEOUT):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_departure_triggers_one_arrival_at_gate(newcomers):
    engine, town, task = await start(3, 3)
    engine.leave("v0")
    await wait_for(lambda: engine.added)
    await asyncio.sleep(SETTLE)
    assert engine.added == [("n1", GATE)]
    assert town.alive_count == 3
    await stop(task)


@pytest.mark.asyncio
async def test_count_returns_to_target_after_several_departures(newcomers):
    engine, town, task = await start(5, 5)
    for vid in ("v0", "v1", "v2"):
        engine.leave(vid)
    await wait_for(lambda: town.alive_count == 5)
    await asyncio.sleep(SETTLE)
    assert town.alive_count == 5
    assert len(engine.added) == 3
    await stop(task)


@pytest.mark.asyncio
async def test_no_arrival_when_population_already_full(newcomers):
    engine, town, task = await start(3, 3)
    engine.departures.put_nowait("ghost")
    await asyncio.sleep(SETTLE + 0.05)
    assert engine.added == []
    await stop(task)


@pytest.mark.asyncio
async def test_arrival_line_reaches_the_square(newcomers):
    engine, town, task = await start(3, 3)
    town.move("v1", "tavern")
    engine.leave("v0")
    await wait_for(lambda: engine.delivered)
    utt, listeners = engine.delivered[0]
    assert (utt.speaker, utt.verb, utt.place, utt.parent) == ("town", "announced", GATE, None)
    assert utt.text == "N1, a cobbler, has arrived at the gate."
    assert listeners == ["v2"]
    assert engine.lineage.root(utt.id) == utt.id
    await stop(task)


@pytest.mark.asyncio
async def test_departed_id_is_not_reused_and_relations_are_living(monkeypatch):
    class Writer:
        async def complete(self, system, prompt, max_tokens):
            return json.dumps({
                "name": "Newt", "occupation": "cobbler", "personality": "shy",
                "grudge": "none", "needs": ["a", "b", "c"],
            })

    engine, town, task = await start(3, 3)
    town.add(make("v9"), START_PLACE)
    task.cancel()
    rep = Replenisher(engine, town, Writer(), Config(population=4, arrival_delay_seconds=0.05), random.Random(1))
    task = asyncio.create_task(rep.run())
    engine.leave("v9")
    await wait_for(lambda: engine.added)
    new_id = engine.added[0][0]
    assert new_id == "v010"
    assert set(town.get(new_id).relationships) <= {"v0", "v1", "v2"}
    await stop(task)


@pytest.mark.asyncio
async def test_failure_is_logged_and_next_departure_tops_up(monkeypatch, caplog):
    calls = []

    async def flaky(writer, existing, rng, departed=()):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("bad reply")
        return make(f"n{len(calls)}")

    monkeypatch.setattr(population_mod, "generate_newcomer", flaky)
    engine, town, task = await start(3, 3)
    with caplog.at_level(logging.ERROR):
        engine.leave("v0")
        await wait_for(lambda: calls)
        await asyncio.sleep(SETTLE)
    assert engine.added == []
    assert "bad reply" in caplog.text
    engine.leave("v1")
    await wait_for(lambda: town.alive_count == 3)
    assert len(engine.added) == 2
    await stop(task)


@pytest.mark.asyncio
async def test_newcomer_needs_are_calibrated_before_joining(newcomers):
    engine, town, task = await start(3, 3)
    engine.leave("v0")
    await wait_for(lambda: engine.added)
    assert engine.baselines.loaded == [(["bread"], 0)]
    await stop(task)


class StubRoutine:
    def __init__(self):
        self.added = []

    async def add(self, v):
        self.added.append(v.id)


@pytest.mark.asyncio
async def test_a_newcomer_gets_a_routine_on_arrival(newcomers):
    engine, town, task = await start(3, 3)
    engine.routine = StubRoutine()
    engine.leave("v0")
    await wait_for(lambda: engine.added)
    assert engine.routine.added == ["n1"]
    await stop(task)


@pytest.mark.asyncio
async def test_no_arrival_while_nobody_is_playing(newcomers):
    engine, town, task = await start(3, 3)
    engine.players = Players(None)
    engine.leave("v0")
    await asyncio.sleep(SETTLE + 0.05)
    assert engine.added == []
    await stop(task)
