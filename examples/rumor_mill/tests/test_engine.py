import asyncio
import logging

import pytest

from examples.rumor_mill import engine as engine_mod
from examples.rumor_mill.actions import IGNORE
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.engine import Engine
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.town import Town

SETTLE = 0.1
TIMEOUT = 2.0


class FakeMind:
    def __init__(self, villager):
        self.villager = villager
        self.queue: asyncio.Queue = asyncio.Queue()
        self.results: list[list[Match]] = []
        self.matches: list[Match] = []
        self.heard = []
        self.polls = 0

    async def open(self):
        pass

    async def hear(self, key, data):
        self.heard.append((key, data))

    async def poll(self):
        self.polls += 1
        fresh = self.results.pop(0) if self.results else [Match("k", {"id": "u-0", "text": "news"})]
        self.matches = fresh
        return fresh

    async def renew(self):
        pass

    async def pushes(self):
        while True:
            yield await self.queue.get()


class RecordingFeed:
    def __init__(self):
        self.deliveries = []

    def on_delivery(self, utterance, listener_ids):
        self.deliveries.append((utterance.id, listener_ids))


class Turns:
    def __init__(self, fail_first=False, leave=False):
        self.views = []
        self.acts = []
        self.fail_first = fail_first
        self.leave = leave

    async def decide(self, jev, view):
        self.views.append(view)
        if self.fail_first and len(self.views) == 1:
            raise RuntimeError("jev down")
        return Decision(IGNORE, None, None, None)

    async def act(self, decision, villager, *, town, **kw):
        self.acts.append((villager.id, kw["inspiration"]))
        if self.leave:
            town.remove(villager.id)


def make(vid):
    return Villager(vid, vid.upper(), "baker", "calm", "none", ["bread"], {})


@pytest.fixture
def turns(monkeypatch):
    t = Turns()
    monkeypatch.setattr(engine_mod, "decide", t.decide)
    monkeypatch.setattr(engine_mod, "act", t.act)
    return t


async def setup(*vids, cooldown=0.05):
    minds = {}

    def factory(v):
        minds[v.id] = FakeMind(v)
        return minds[v.id]

    feed = RecordingFeed()
    engine = Engine(
        Config(cooldown_seconds=cooldown), Town(), None, None, None, Lineage(), Ids(), feed,
        mind_factory=factory,
    )
    for vid in vids:
        await engine.add_villager(make(vid))
    await engine.run()
    return engine, minds, feed


async def wait_for(predicate, timeout=TIMEOUT):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def push(mind, n=1):
    for _ in range(n):
        mind.queue.put_nowait(None)


@pytest.mark.asyncio
async def test_push_with_no_new_matches_makes_no_turn(turns):
    engine, minds, _ = await setup("v1")
    minds["v1"].results = [[]]
    push(minds["v1"])
    await wait_for(lambda: minds["v1"].polls == 1)
    await asyncio.sleep(SETTLE)
    assert minds["v1"].results == []
    assert turns.views == []
    await engine.stop()


@pytest.mark.asyncio
async def test_pushes_during_cooldown_coalesce_into_one_turn(turns):
    engine, minds, _ = await setup("v1", cooldown=1.0)
    push(minds["v1"])
    await wait_for(lambda: len(turns.views) == 1)
    push(minds["v1"], 3)
    await asyncio.sleep(SETTLE)
    assert len(turns.views) == 1
    await wait_for(lambda: len(turns.views) == 2)
    await asyncio.sleep(SETTLE)
    assert len(turns.views) == 2
    assert minds["v1"].polls == 2
    await engine.stop()


@pytest.mark.asyncio
async def test_turn_exception_is_logged_and_worker_survives(turns, caplog):
    turns.fail_first = True
    engine, minds, _ = await setup("v1")
    with caplog.at_level(logging.ERROR):
        push(minds["v1"])
        await wait_for(lambda: "turn failed for v1" in caplog.text)
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 1)
    await engine.stop()


@pytest.mark.asyncio
async def test_departed_villager_worker_ends(turns):
    turns.leave = True
    engine, minds, _ = await setup("v1")
    push(minds["v1"])
    await wait_for(lambda: "v1" not in engine.minds)
    push(minds["v1"])
    await asyncio.sleep(SETTLE)
    assert len(turns.acts) == 1
    assert minds["v1"].polls == 1
    await engine.stop()


@pytest.mark.asyncio
async def test_deliver_skips_departed_listeners_and_notifies_feed(turns):
    engine, minds, feed = await setup("v1", "v2", "v3")
    engine.town.remove("v3")
    utt = Utterance("u-1", "v1", "V1", "square", "hello", None)
    await engine.deliver(utt, ["v2", "v3"])
    assert minds["v2"].heard == [("utt:u-1", utt.to_data(0))]
    assert minds["v3"].heard == []
    assert feed.deliveries == [("u-1", ["v2"])]
    await engine.stop()


@pytest.mark.asyncio
async def test_on_heard_called_with_new_matches(turns):
    engine, minds, _ = await setup("v1")
    heard = []
    engine.on_heard = lambda vid, matches: heard.append((vid, [m.data_key for m in matches]))
    minds["v1"].results = [[Match("a", {"id": "u-1", "text": "x"})], []]
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 1)
    push(minds["v1"])
    await wait_for(lambda: minds["v1"].polls == 2)
    assert heard == [("v1", ["a"])]
    assert turns.acts == [("v1", ["x"])]
    await engine.stop()


@pytest.mark.asyncio
async def test_stop_leaves_no_pending_tasks(turns):
    engine, minds, _ = await setup("v1", "v2")
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 1)
    await engine.stop()
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert pending == []


@pytest.mark.asyncio
async def test_remove_villager_enqueues_departure_once(turns):
    engine, _, _ = await setup("v1", "v2")
    await engine.remove_villager("v1")
    await engine.remove_villager("v1")
    assert engine.departures.qsize() == 1
    assert engine.departures.get_nowait() == "v1"
    await engine.stop()
