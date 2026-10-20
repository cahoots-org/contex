import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest

from examples.rumor_mill import engine as engine_mod
from examples.rumor_mill.actions import IGNORE
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.engine import Engine
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.mind import Mind, mind_project
from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.player import Player
from examples.rumor_mill.suspicion import Suspicion
from examples.rumor_mill.tests.helpers import FakeEngine, StubBaselines
from examples.rumor_mill.town import Town

URL = os.environ.get("RUMOR_MILL_CONTEX_URL")
pytestmark = [
    pytest.mark.skipif(not URL, reason="RUMOR_MILL_CONTEX_URL not set"),
    pytest.mark.asyncio,
]


def project() -> str:
    return f"rumor-mill:test-{uuid.uuid4().hex[:8]}:mind:x"


async def poll_for(client, uri, key, tries=20):
    for _ in range(tries):
        keys = {m.data_key for ms in (await client.read(uri)).values() for m in ms}
        if key in keys:
            return True
        await asyncio.sleep(0.5)
    return False


async def delayed_publish(client, pid):
    await asyncio.sleep(1)
    await client.publish(pid, "u2", {"text": "the baker's oven is cold again"})


async def test_publish_subscribe_read_matches_by_meaning():
    async with ContexClient(URL) as client:
        pid = project()
        uri = await client.subscribe(pid, ["anything about my bakery"], max_age_seconds=600, top_k=5)
        await client.publish(pid, "u1", {"text": "the oven at the baker's shop is cold"})
        assert await poll_for(client, uri, "u1")


async def test_push_arrives_on_publish():
    async with ContexClient(URL) as client:
        pid = project()
        uri = await client.subscribe(pid, ["anything about my bakery"], max_age_seconds=600, top_k=5)
        events = client.listen(uri)
        async with asyncio.timeout(30):
            await anext(events)
            publish = asyncio.create_task(delayed_publish(client, pid))
            await anext(events)
        await events.aclose()
        await publish


async def test_mind_projects_are_isolated():
    async with ContexClient(URL) as client:
        a, b = project(), project()
        uri_a = await client.subscribe(a, ["anything about my bakery"], max_age_seconds=600, top_k=5)
        uri_b = await client.subscribe(b, ["anything about my bakery"], max_age_seconds=600, top_k=5)
        await client.publish(a, "u3", {"text": "the baker's oven is cold"})
        assert await poll_for(client, uri_a, "u3")
        assert not await poll_for(client, uri_b, "u3", tries=3)


class NullFeed:
    def on_delivery(self, utterance, listener_ids):
        pass


async def test_publish_before_listening_still_wakes_only_the_listener(monkeypatch):
    woke = []
    turned = asyncio.Event()

    async def decide(jev, view):
        woke.append((view.villager.id, [m.data_key for m in view.new]))
        turned.set()
        return Decision(IGNORE, None, None, None)

    async def act(*args, **kwargs):
        pass

    monkeypatch.setattr(engine_mod, "decide", decide)
    monkeypatch.setattr(engine_mod, "act", act)
    async with ContexClient(URL) as client:
        config = Config(contex_url=URL, session=f"test-{uuid.uuid4().hex[:8]}", cooldown_seconds=0.1)
        baselines = StubBaselines(0.5)
        engine = Engine(config, Town(), client, None, None, Lineage(), Ids(), NullFeed(), baselines=baselines)
        ann = Villager("v1", "Ann", "baker", "kind", "none", ["anything about my bakery"], {})
        early = Mind(client, ann, config, baselines)
        await early.open()
        utt = Utterance("u-1", "v2", "Bo", "square", "the oven at the bakery is cold", None)
        await early.hear("utt:u-1", utt.to_data(0))
        assert await poll_for(client, early.uris[0], "utt:u-1")
        await engine.add_villager(ann)
        await engine.add_villager(Villager("v2", "Bo", "smith", "gruff", "none", ["anything about the forge"], {}))
        await engine.run()
        async with asyncio.timeout(30):
            await turned.wait()
        await engine.stop()
    assert woke == [("v1", ["utt:u-1"])]


async def test_query_threshold_filters_unrelated_matches():
    async with ContexClient(URL) as client:
        pid = project()
        await client.publish(pid, "u1", {"text": "the oven at the baker's shop is cold"})
        assert await client.query(pid, "quantum chromodynamics lecture", top_k=3)
        assert not await client.query(pid, "quantum chromodynamics lecture", top_k=3, threshold=0.99)


async def test_default_threshold_lets_only_whispers_on_a_need_in():
    async with ContexClient(URL) as client:
        config = Config(contex_url=URL, session=f"test-{uuid.uuid4().hex[:8]}")
        marta = Villager("v1", "Marta", "baker", "anxious", "none", [
            "Has anyone been saying my bread is stale or my oven unsafe?",
            "I dread omens, curses, and anything wrong with the well water.",
        ], {})
        mind = Mind(client, marta, config, StubBaselines(0.815))
        await mind.open()
        off = Utterance("w-1", "newcomer", "The newcomer", "square",
                        "lattice gauge theory predicts quark confinement at low energies", None, "whispered to you")
        on = Utterance("w-2", "newcomer", "The newcomer", "square",
                       "the well has been cursed and everyone who drinks from it falls ill", None, "whispered to you")
        await mind.hear("utt:w-1", off.to_data(0))
        await mind.hear("utt:w-2", on.to_data(0))
        assert await poll_for(client, mind.uris[1], "utt:w-2")
        assert "utt:w-1" not in {m.data_key for m in mind.matches + await mind.poll()}


async def test_each_need_has_its_own_subscription():
    async with ContexClient(URL) as client:
        config = Config(contex_url=URL, session=f"test-{uuid.uuid4().hex[:8]}")
        bakery, physics = "anything about my bakery", "quantum chromodynamics lecture"
        ann = Villager("v1", "Ann", "baker", "kind", "none", [bakery, physics], {})
        mind = Mind(client, ann, config, StubBaselines(0.5, **{physics: 0.95}))
        await mind.open()
        assert len(set(mind.uris)) == 2
        await mind.hear("utt:b", {"text": "the oven at the baker's shop is cold"})
        assert await poll_for(client, mind.uris[0], "utt:b")
        bundle = await mind._read()
        assert {m.data_key for m in bundle[bakery]} == {"utt:b"}
        assert bundle.get(physics, []) == []
        assert [m.data_key for m in await mind.poll()] == ["utt:b"]


async def test_query_since_excludes_older_items():
    async with ContexClient(URL) as client:
        pid = project()
        await client.publish(pid, "old", {"text": "the oven at the baker's shop is cold"})
        await asyncio.sleep(1)
        cutoff = datetime.now(UTC).isoformat()
        await asyncio.sleep(1)
        await client.publish(pid, "new", {"text": "the oven at the baker's shop is cold again"})
        everything = await client.query(pid, "the baker's oven", top_k=5)
        recent = await client.query(pid, "the baker's oven", top_k=5, since=cutoff)
        assert {m.data_key for m in everything} == {"old", "new"}
        assert [m.data_key for m in recent] == ["new"]
        assert recent[0].similarity is not None


async def test_direct_check_hits_only_the_need_it_clears():
    bakery, physics = "anything about my bakery", "quantum chromodynamics lecture"
    async with ContexClient(URL) as client:
        config = Config(contex_url=URL, session=f"test-{uuid.uuid4().hex[:8]}")
        town = Town()
        town.add(Villager("v1", "Ann", "baker", "kind", "none", [physics, bakery], {}), "square")
        town.add(Villager("v2", "Bo", "scholar", "dry", "none", [physics], {}), "square")
        engine = FakeEngine(Suspicion(config, town), client, StubBaselines(0.99, **{bakery: 0.5}))

        async def deliver(utterance, listener_ids):
            for vid in listener_ids:
                await client.publish(mind_project(config.session, vid), f"utt:{utterance.id}", utterance.to_data(0))

        engine.deliver = deliver
        lineage = Lineage()
        player = Player(engine, town, lineage, Ids(), Feed(lineage, town, out=None), config)
        assert await player.whisper_to("v1", "the oven at the bakery is cold") is None
        assert await player.whisper_to("v2", "the oven at the bakery is cold") is None
        player.close()
    (vid, match), = engine.direct
    assert (vid, match.data_key) == ("v1", "utt:w-1") and match.similarity >= 0.5
    states = {(e.data["whisper_id"], e.data["state"]) for e in player.feed.recent if e.kind == "whisper_status"}
    assert {("w-1", "heard"), ("w-2", "brushed_off")} <= states
