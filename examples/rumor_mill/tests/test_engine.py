import asyncio
import logging

import pytest

from examples.rumor_mill import engine as engine_mod
from examples.rumor_mill.actions import IGNORE
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.engine import Engine
from examples.rumor_mill.game import Game
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
        self.strength = None

    async def decide(self, jev, view):
        self.views.append(view)
        if self.fail_first and len(self.views) == 1:
            raise RuntimeError("jev down")
        return Decision(IGNORE, None, None, None, self.strength)

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


async def setup(*vids, cooldown=0.05, game=None):
    minds = {}

    def factory(v):
        minds[v.id] = FakeMind(v)
        return minds[v.id]

    feed = RecordingFeed()
    engine = Engine(
        Config(cooldown_seconds=cooldown), Town(), None, None, None, Lineage(), Ids(), feed,
        mind_factory=factory, game=game,
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
    engine, minds, _ = await setup("v1", "v2", game=Game(RecordingFeed()))
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


@pytest.mark.asyncio
async def test_failed_open_leaves_town_and_engine_unchanged(turns):
    engine, _, _ = await setup("v1")

    class BrokenMind(FakeMind):
        async def open(self):
            raise RuntimeError("contex down")

    engine.mind_factory = BrokenMind
    with pytest.raises(RuntimeError):
        await engine.add_villager(make("v2"))
    assert engine.town.alive_count == 1
    assert [v.id for v in engine.town.villagers] == ["v1"]
    assert list(engine.minds) == ["v1"]
    assert list(engine._tasks) == ["v1"]
    await engine.stop()


class FailingMind(FakeMind):
    async def hear(self, key, data):
        raise RuntimeError("contex down")


@pytest.mark.asyncio
async def test_one_failing_listener_does_not_hide_the_others(turns, caplog):
    engine, minds, feed = await setup("v1", "v2", "v3")
    engine.minds["v3"] = FailingMind(engine.town.get("v3"))
    utt = Utterance("u-1", "v1", "V1", "square", "hello", None)
    with caplog.at_level(logging.ERROR):
        await engine.deliver(utt, ["v2", "v3"])
    assert minds["v2"].heard == [("utt:u-1", utt.to_data(0))]
    assert feed.deliveries == [("u-1", ["v2"])]
    assert "v3" in caplog.text
    await engine.stop()


@pytest.mark.asyncio
async def test_deliver_raises_when_every_listener_fails(turns):
    engine, _, feed = await setup("v1", "v2")
    engine.minds["v2"] = FailingMind(engine.town.get("v2"))
    utt = Utterance("u-1", "v1", "V1", "square", "hello", None)
    with pytest.raises(RuntimeError):
        await engine.deliver(utt, ["v2"])
    assert feed.deliveries == []
    await engine.stop()


@pytest.mark.asyncio
async def test_repeated_listen_failures_log_one_traceback(turns, monkeypatch, caplog):
    monkeypatch.setattr(engine_mod, "LISTEN_RETRY_SECONDS", 0.01)
    engine, minds, _ = await setup("v1")

    async def broken():
        raise RuntimeError("contex down")
        yield

    minds["v1"].pushes = broken
    with caplog.at_level(logging.WARNING):
        await wait_for(lambda: len(caplog.records) >= 3)
    await engine.stop()
    with_trace = [r for r in caplog.records if r.exc_info]
    assert len(with_trace) == 1
    assert all("v1" in r.getMessage() for r in caplog.records)


def utterance():
    return Utterance("u-1", "v1", "V1", "square", "watch the newcomer", None)


@pytest.mark.asyncio
async def test_addressed_message_wakes_a_villager_whose_needs_did_not_match(turns):
    engine, minds, _ = await setup("v1", "v2")
    minds["v2"].results = [[]]
    utt = utterance()
    await engine.deliver(utt, ["v2"], addressed=True)
    await wait_for(lambda: len(turns.views) == 1)
    view = turns.views[0]
    assert view.villager.id == "v2"
    assert view.new == [Match("utt:u-1", utt.to_data(0))]
    assert minds["v2"].heard == [("utt:u-1", utt.to_data(0))]
    await engine.stop()


@pytest.mark.asyncio
async def test_unaddressed_delivery_does_not_bypass_matching(turns):
    engine, minds, _ = await setup("v1", "v2")
    await engine.deliver(utterance(), ["v2"])
    await asyncio.sleep(SETTLE)
    assert minds["v2"].polls == 0
    assert turns.views == []
    assert engine._pending == {}
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_message_also_matched_by_poll_appears_once(turns):
    engine, minds, _ = await setup("v1", "v2")
    utt = utterance()
    minds["v2"].results = [[Match("utt:u-1", utt.to_data(0))]]
    await engine.deliver(utt, ["v2"], addressed=True)
    await wait_for(lambda: len(turns.views) == 1)
    assert [m.data_key for m in turns.views[0].new] == ["utt:u-1"]
    assert turns.views[0].recent == []
    await engine.stop()


@pytest.mark.asyncio
async def test_pending_is_cleared_after_the_turn(turns):
    engine, minds, _ = await setup("v1", "v2")
    minds["v2"].results = [[], []]
    await engine.deliver(utterance(), ["v2"], addressed=True)
    await wait_for(lambda: len(turns.views) == 1)
    assert "v2" not in engine._pending
    push(minds["v2"])
    await wait_for(lambda: minds["v2"].polls == 2)
    await asyncio.sleep(SETTLE)
    assert len(turns.views) == 1
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_message_during_cooldown_is_kept_for_the_next_turn(turns):
    engine, minds, _ = await setup("v1", "v2", cooldown=1.0)
    push(minds["v2"])
    await wait_for(lambda: len(turns.views) == 1)
    minds["v2"].results = [[]]
    await engine.deliver(utterance(), ["v2"], addressed=True)
    await asyncio.sleep(SETTLE)
    assert len(turns.views) == 1
    assert [m.data_key for m in engine._pending["v2"]] == ["utt:u-1"]
    await wait_for(lambda: len(turns.views) == 2)
    assert [m.data_key for m in turns.views[1].new] == ["utt:u-1"]
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_message_to_a_departed_villager_is_dropped_quietly(turns):
    engine, minds, feed = await setup("v1", "v2")
    await engine.remove_villager("v2")
    await engine.deliver(utterance(), ["v2"], addressed=True)
    assert engine._pending == {}
    assert "v2" not in engine._wakes
    assert minds["v2"].heard == []
    assert feed.deliveries == [("u-1", [])]
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_listener_who_leaves_during_delivery_is_skipped(turns):
    engine, minds, feed = await setup("v1", "v2", "v3")
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_hear(key, data):
        started.set()
        await release.wait()

    minds["v2"].hear = slow_hear
    delivery = asyncio.create_task(engine.deliver(utterance(), ["v2", "v3"], addressed=True))
    await started.wait()
    await engine.remove_villager("v2")
    release.set()
    await delivery
    assert "v2" not in engine._pending
    assert feed.deliveries == [("u-1", ["v2", "v3"])]
    await wait_for(lambda: [v.villager.id for v in turns.views] == ["v3"])
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_message_survives_a_failed_poll(turns):
    engine, minds, _ = await setup("v1", "v2")
    mind = minds["v2"]
    real_poll = mind.poll
    mind.results = [[]]

    async def flaky_poll():
        if mind.polls == 0:
            mind.polls += 1
            raise RuntimeError("contex down")
        return await real_poll()

    mind.poll = flaky_poll
    await engine.deliver(utterance(), ["v2"], addressed=True)
    await wait_for(lambda: len(turns.views) == 1)
    assert [m.data_key for m in turns.views[0].new] == ["utt:u-1"]
    await engine.stop()


@pytest.mark.asyncio
async def test_suspicion_strength_updates_scores_and_a_rating_error_does_not_fail_the_turn(turns, caplog):
    engine, minds, _ = await setup("v1")
    engine.lineage.record("w-1", None, player=True)
    engine.lineage.record("u-0", "w-1")
    turns.strength = 1.0
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 1)
    view = turns.views[0]
    assert view.player_rooted == view.new
    assert (view.suspicion, view.warn_threshold) == (0.0, engine.config.warn_threshold)
    assert engine.suspicion.score("v1") == engine.config.suspicion_hear_max

    def broken(*args):
        raise RuntimeError("rating down")

    engine.suspicion.rate = broken
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 2)
    assert turns.views[1].suspicion == engine.config.suspicion_hear_max
    assert "rating down" in caplog.text
    engine.suspect("v1", 0.1, "was warned about the newcomer", "u-1")
    assert engine.suspicion.score("v1") == pytest.approx(engine.config.suspicion_hear_max + 0.1)
    await engine.stop()


@pytest.mark.asyncio
async def test_no_turns_start_after_the_game_is_over(turns):
    game = Game(RecordingFeed())
    engine, minds, _ = await setup("v1", game=game)
    game.finished.set()
    push(minds["v1"])
    await asyncio.sleep(SETTLE)
    assert (turns.views, minds["v1"].polls) == ([], 0)
    await engine.stop()


@pytest.mark.asyncio
async def test_a_turn_still_deciding_when_the_game_ends_does_nothing(turns, monkeypatch):
    game = Game(RecordingFeed())
    engine, minds, _ = await setup("v1", game=game)
    engine.lineage.record("w-1", None, player=True)
    engine.lineage.record("u-0", "w-1")
    deciding, release = asyncio.Event(), asyncio.Event()

    async def slow_decide(jev, view):
        deciding.set()
        await release.wait()
        return Decision(IGNORE, None, None, None, 1.0)

    monkeypatch.setattr(engine_mod, "decide", slow_decide)
    push(minds["v1"])
    await asyncio.wait_for(deciding.wait(), TIMEOUT)
    game.finished.set()
    release.set()
    await asyncio.sleep(SETTLE)
    assert turns.acts == []
    assert engine.suspicion.score("v1") == 0.0
    assert engine._pending.get("v1") is None
    await engine.stop()
