import asyncio
import json
import logging
import os
import time
from types import SimpleNamespace

import httpx
import pytest

from examples.whispers import engine as engine_mod
from examples.whispers import persist, web
from examples.whispers.actions import IGNORE
from examples.whispers.config import Config
from examples.whispers.contex_client import Match
from examples.whispers.decide import Decision
from examples.whispers.engine import Engine
from examples.whispers.facts import Facts
from examples.whispers.feed import Feed
from examples.whispers.game import Game
from examples.whispers.goals import Trophy
from examples.whispers.judge import Judge
from examples.whispers.lineage import Ids, Lineage
from examples.whispers.models import Utterance, Villager
from examples.whispers.overhear import Overhear
from examples.whispers.persist import components, load_game, restore_game, save_every, save_game, state_path, stop
from examples.whispers.player import Player
from examples.whispers.players import Players
from examples.whispers.rumors import Rumors
from examples.whispers.tests.helpers import Always, FakeMind, StubBaselines, StubContex, goal_maker, wait_for
from examples.whispers.town import Town
from examples.whispers.usage import Usage
from examples.whispers.web import create_app

SESSION = "s1"
NOW = 1_800_000_000.0
WHISPER = "the miller waters his flour"
RETELLING = "they say the miller waters his flour"
VILLAGERS = [
    Villager("v1", "Marta Hale", "herbalist", "anxious", "none", ["bread"], {}),
    Villager("v2", "Bert Cole", "miller", "jolly", "none", ["flour"], {}),
    Villager("v3", "Ida Fenn", "smith", "grim", "none", ["iron"], {}),
    Villager("v4", "Ned Rowe", "baker", "calm", "none", ["yeast"], {}),
]


async def ignore(jev, view):
    return Decision(IGNORE, None, None, None)


def build(contex=None):
    """A game wired as __main__ wires it, with stub Contex, Jev and writer and no villagers yet."""
    config = Config(session=SESSION)
    contex = contex or StubContex()
    town, lineage, ids, facts = Town(), Lineage(), Ids(), Facts()
    feed = Feed(lineage, town, out=None)
    game, usage, baselines = Game(config.memory_moves, config.day_seconds), Usage(config), StubBaselines(0.5)
    overhear = Overhear(contex, baselines, config, Always(0.99))
    engine = Engine(
        config, town, contex, None, None, lineage, ids, feed, mind_factory=FakeMind, game=game, baselines=baselines,
        rumors=Rumors(contex, baselines, config, overhear), overhear=overhear, facts=facts,
    )
    maker = goal_maker(town, ids=ids)
    judge = Judge(contex, None, town, config, feed, game, baselines=baselines, lineage=lineage, facts=facts,
                  engine=engine, maker=maker)
    players = Players(feed, usage)
    engine.players, engine.judge = players, judge

    def make(pid, owner):
        return Player(engine, town, lineage, ids, feed, config, None, game, player_id=pid, owner=owner)

    return SimpleNamespace(engine=engine, judge=judge, players=players, make=make, parts=components(engine), feed=feed)


async def api_state(g, owner):
    app = create_app(
        players=g.players, join=None, town=g.engine.town, feed=g.feed, judge=g.judge, usage=g.players.usage, port=8023,
        suspicion=g.engine.suspicion, game=g.engine.game, rumors=g.engine.rumors, hints=g.engine.hints,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8023") as client:
        client.cookies.set(web.COOKIE, owner)
        state = (await client.get("/api/state")).json()
    del state["boot"]
    return state


async def played(monkeypatch):
    """A scripted game: a player whispers, a villager retells, a goal is met and trophies are won."""
    monkeypatch.setattr(time, "time", lambda: NOW)
    monkeypatch.setattr(engine_mod, "decide", ignore)
    contex = StubContex({
        "bread": [Match("utt:w-1", {}, 0.9)],
        RETELLING: [Match("rumor:w-1", {}, 0.9)],
    })
    g = build(contex)
    for v in VILLAGERS:
        await g.engine.add_villager(v, "square")
    g.engine.town.found_group("v2", "Millers", "flour first")
    player = g.make("p-abc", "o-abc")
    g.players.add(player)
    player.connected()
    g.judge.deal(player)
    assert await player.whisper_to("v1", WHISPER) is None
    retold = Utterance(g.engine.ids.next("u"), "v1", "Marta Hale", "square", RETELLING, "w-1", "retold",
                       depth=1, action="retell")
    g.engine.lineage.record(retold.id, "w-1")
    g.engine.facts.record(retold)
    await g.engine.deliver(retold, ["v2"])
    goal = player.goals[0]
    player.met.add(goal.id)
    g.feed.on_met(goal, player=player.id)
    rising = player.goals[1]
    g.judge.progress[rising.id], g.judge.counts[rising.id] = 0.5, (1, 2)
    g.feed.emit("scene", "", player=player.id, scene_id="s-4")
    g.feed.emit("overheard", "", player=player.id, scene="s-7")
    g.players.award("o-abc", Trophy("personal", ("a",), 1), "A trophy.")
    g.players.award("o-gone", Trophy("world", ("b",), 1), "Another trophy.")
    player.disconnected()
    await g.engine.stop()
    return g, player


def assert_next_ids_are_new(fresh, g, player):
    ids = fresh.engine.ids
    assert ids.next("w") == "w-2" and ids.next("u") == "u-2"
    assert ids.next("world") == f"world-{int(g.judge.world.id.split('-')[1]) + 1}"
    assert ids.next("goal") == f"goal-{max(int(gl.id.rsplit('-', 1)[1]) for gl in player.goals) + 1}"


@pytest.mark.asyncio
async def test_a_played_game_restores_to_the_same_state_with_fresh_ids(monkeypatch, tmp_path):
    g, player = await played(monkeypatch)
    card = g.engine.rumors._cards["w-1"]
    assert card.hops and g.players.pending["o-gone"] and player.met
    before = await api_state(g, player.owner)
    assert before["goals"][1]["percent"] == 50
    path = state_path(SESSION, str(tmp_path))
    save_game(path, g.parts, SESSION)

    fresh = build()
    assert restore_game(load_game(path, SESSION), fresh.parts, fresh.make)
    restored = fresh.players.get(player.id)
    assert await api_state(fresh, player.owner) == before
    assert fresh.players.pending == g.players.pending and fresh.players.cases == g.players.cases
    assert fresh.parts["town"].groups == g.engine.town.groups
    assert not fresh.players.any_playing()
    restored.connected()
    assert fresh.players.any_playing()

    assert_next_ids_are_new(fresh, g, player)
    assert fresh.feed.say("next").id == g.feed.recent[-1].id + 1
    assert fresh.engine.town.found_group("v1", "New", "x").id == "g-2"
    assert fresh.engine.scenes._count == 7


@pytest.mark.asyncio
async def test_counters_resume_past_every_id_in_use_even_when_saved_counters_lag(monkeypatch, tmp_path):
    g, player = await played(monkeypatch)
    path = state_path(SESSION, str(tmp_path))
    save_game(path, g.parts, SESSION)
    state = load_game(path, SESSION)
    state["ids"] = {}
    state["feed"]["last_id"] = 0
    state["feed"]["closed"] = {"p-old": 999}
    state["hints"]["last_key"] = 7

    fresh = build()
    assert restore_game(state, fresh.parts, fresh.make)
    assert_next_ids_are_new(fresh, g, player)
    assert fresh.feed.say("next").id == 1000
    await fresh.engine.hints.add("p-abc", "v1", "line")
    assert fresh.engine.contex.published[-1][1] == "hint:8"


def test_a_save_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    g = build()
    path = state_path(SESSION, str(tmp_path))
    save_game(path, g.parts, SESSION)
    before = open(path).read()

    def torn(state, f):
        f.write('{"version": 1, "sess')
        raise OSError("disk full")

    monkeypatch.setattr(persist.json, "dump", torn)
    with pytest.raises(OSError):
        save_game(path, g.parts, SESSION)
    assert open(path).read() == before
    assert load_game(path, SESSION) is not None


@pytest.mark.parametrize("content", [None, "{not json", json.dumps({"version": 99, "session": SESSION}),
                                     json.dumps({"version": persist.STATE_VERSION, "session": "other"}), "[]"])
def test_a_save_that_cannot_be_used_is_logged_and_ignored(tmp_path, caplog, content):
    path = state_path(SESSION, str(tmp_path))
    if content is not None:
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write(content)
    with caplog.at_level(logging.INFO, logger="examples.whispers.persist"):
        assert load_game(path, SESSION) is None
    assert "starting fresh" in caplog.text


@pytest.mark.asyncio
async def test_a_save_that_fails_to_restore_leaves_the_game_fresh(monkeypatch, tmp_path, caplog):
    g, player = await played(monkeypatch)
    path = state_path(SESSION, str(tmp_path))
    save_game(path, g.parts, SESSION)
    state = load_game(path, SESSION)
    del state["judge"]["world"]

    fresh = build()
    for v in VILLAGERS:
        fresh.engine.town.add(v)
    world = fresh.judge.world
    assert not restore_game(state, fresh.parts, fresh.make)
    assert "could not be restored" in caplog.text
    assert fresh.players.get(player.id) is None and not fresh.feed.recent
    assert fresh.judge.world == world and fresh.engine.ids.next("w") == "w-1"
    assert [v.id for v in fresh.engine.town.villagers] == [v.id for v in VILLAGERS]


@pytest.mark.asyncio
async def test_a_stop_signal_saves_then_cancels_the_game(tmp_path):
    g = build()
    path = state_path(SESSION, str(tmp_path))
    task = asyncio.create_task(asyncio.Event().wait())
    stop(lambda: save_game(path, g.parts, SESSION), task)
    assert load_game(path, SESSION)["session"] == SESSION
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_the_game_is_saved_every_interval():
    saves = []
    task = asyncio.create_task(save_every(0, lambda: saves.append(1)))
    await wait_for(lambda: len(saves) >= 3)
    task.cancel()
