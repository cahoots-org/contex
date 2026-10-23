import asyncio

import pytest

from examples.whispers import mind as mind_mod
from examples.whispers.calibration import Baselines
from examples.whispers.config import Config
from examples.whispers.contex_client import Match, SubscriptionGone
from examples.whispers.mind import Mind, mind_project
from examples.whispers.models import Villager

CONFIG = Config(day_seconds=100, memory_days=2, match_margin=0.05)


def uri_of(need):
    return f"contex://subscriptions/{need}"


class StubClient:
    """Each need's subscription reads its own list of bundles; the last one repeats."""

    def __init__(self, bundles, gone=()):
        self.bundles = bundles if isinstance(bundles, dict) else {"bread prices": list(bundles)}
        self.gone = {uri_of(n) for n in gone}
        self.subscribes = []
        self.published = []

    async def subscribe(self, project_id, needs, **kwargs):
        self.subscribes.append((project_id, needs, kwargs))
        return uri_of(needs[0])

    async def read(self, uri):
        if uri in self.gone:
            self.gone.discard(uri)
            raise SubscriptionGone(uri)
        queue = self.bundles[uri.rsplit("/", 1)[1]]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    async def publish(self, project_id, data_key, data):
        self.published.append((project_id, data_key, data))


def villager(needs=("bread prices",)):
    return Villager("v1", "Ann", "baker", "kind", "none", list(needs), {})


def baselines(**values):
    b = Baselines(None, CONFIG)
    for need, value in {"bread prices": 0.75, **values}.items():
        b.set(need, value)
    return b


def m(key):
    return Match(key, {"text": key})


async def opened(client, needs=("bread prices",), **values):
    mind = Mind(client, villager(needs), CONFIG, baselines(**values))
    await mind.open()
    return mind


@pytest.mark.asyncio
async def test_poll_returns_only_new_matches():
    mind = await opened(StubClient([{"n": [m("a")]}, {"n": [m("a"), m("b")]}]))
    assert [x.data_key for x in await mind.poll()] == ["a"]
    assert [x.data_key for x in await mind.poll()] == ["b"]


@pytest.mark.asyncio
async def test_poll_resubscribes_after_subscription_gone():
    client = StubClient([{"n": [m("a")]}], gone=["bread prices"])
    mind = await opened(client)
    assert [x.data_key for x in await mind.poll()] == ["a"]
    assert len(client.subscribes) == 2
    assert client.subscribes[0] == client.subscribes[1]


@pytest.mark.asyncio
async def test_forgotten_then_returned_item_is_new_again():
    mind = await opened(StubClient([{"n": [m("a")]}, {"n": []}, {"n": [m("a")]}]))
    assert len(await mind.poll()) == 1
    assert await mind.poll() == []
    assert len(await mind.poll()) == 1


@pytest.mark.asyncio
async def test_subscribe_passes_memory_window():
    client = StubClient([{}])
    config = Config(day_seconds=100, memory_days=2, top_k=7, match_margin=0.05)
    await Mind(client, villager(), config, baselines()).open()
    project, needs, kwargs = client.subscribes[0]
    assert project == mind_project(config.session, "v1")
    assert needs == ["bread prices"]
    assert kwargs == {"max_age_seconds": 200, "top_k": 7, "threshold": pytest.approx(0.80)}


@pytest.mark.asyncio
async def test_renew_does_not_swallow_unseen_matches():
    mind = await opened(StubClient([{"n": [m("a")]}]))
    await mind.renew()
    assert [x.data_key for x in await mind.poll()] == ["a"]


@pytest.mark.asyncio
async def test_renew_resubscribes_when_gone():
    client = StubClient([{"n": [m("a")]}], gone=["bread prices"])
    mind = await opened(client)
    await mind.renew()
    assert len(client.subscribes) == 2


@pytest.mark.asyncio
async def test_poll_keeps_whole_bundle_as_matches():
    mind = await opened(StubClient([{"n": [m("a")]}, {"n": [m("a"), m("b")], "o": [m("b")]}]))
    await mind.poll()
    await mind.poll()
    assert [x.data_key for x in mind.matches] == ["a", "b"]


class ReconnectingClient(StubClient):
    def __init__(self, bundles=({},)):
        super().__init__(bundles)
        self.connects = 0
        self.listened = []

    async def listen_many(self, uris):
        self.connects += 1
        self.listened.append(list(uris))
        yield


@pytest.mark.asyncio
async def test_pushes_yields_on_every_reconnect(monkeypatch):
    monkeypatch.setattr(mind_mod, "REOPEN_PAUSE_SECONDS", 0)
    client = ReconnectingClient()
    mind = await opened(client)
    pushes = mind.pushes()
    for _ in range(3):
        await anext(pushes)
    await pushes.aclose()
    assert client.connects == 3


@pytest.mark.asyncio
async def test_open_subscribes_once_per_need_with_its_threshold():
    client = StubClient({"bread": [{}], "omens": [{}]})
    mind = await opened(client, ("bread", "omens"), bread=0.70, omens=0.78)
    assert mind.uris == [uri_of("bread"), uri_of("omens")]
    assert [(needs, kwargs["threshold"]) for _, needs, kwargs in client.subscribes] == [
        (["bread"], pytest.approx(0.75)), (["omens"], pytest.approx(0.83)),
    ]


@pytest.mark.asyncio
async def test_poll_merges_bundles_and_dedupes_keys():
    client = StubClient({
        "bread": [{"bread": [m("a"), m("b")]}],
        "omens": [{"omens": [m("b"), m("c")]}],
    })
    mind = await opened(client, ("bread", "omens"), bread=0.7, omens=0.7)
    assert sorted(x.data_key for x in await mind.poll()) == ["a", "b", "c"]
    assert sorted(x.data_key for x in mind.matches) == ["a", "b", "c"]
    assert await mind.poll() == []


@pytest.mark.asyncio
async def test_one_expired_subscription_is_recreated_alone():
    client = StubClient({"bread": [{"bread": [m("a")]}], "omens": [{"omens": [m("b")]}]}, gone=["omens"])
    mind = await opened(client, ("bread", "omens"), bread=0.7, omens=0.7)
    epoch = mind._epoch
    assert sorted(x.data_key for x in await mind.poll()) == ["a", "b"]
    assert [needs for _, needs, _ in client.subscribes] == [["bread"], ["omens"], ["omens"]]
    assert client.subscribes[1] == client.subscribes[2]
    assert mind.uris == [uri_of("bread"), uri_of("omens")]
    assert mind._epoch == epoch + 1


@pytest.mark.asyncio
async def test_pushes_listens_on_all_uris(monkeypatch):
    monkeypatch.setattr(mind_mod, "REOPEN_PAUSE_SECONDS", 0)
    client = ReconnectingClient({"bread": [{}], "omens": [{}]})
    mind = Mind(client, villager(("bread", "omens")), CONFIG, baselines(bread=0.7, omens=0.7))
    await mind.open()
    pushes = mind.pushes()
    await anext(pushes)
    await pushes.aclose()
    assert client.listened == [[uri_of("bread"), uri_of("omens")]]


@pytest.mark.asyncio
async def test_mind_without_needs_never_listens():
    client = ReconnectingClient()
    mind = Mind(client, villager(()), CONFIG, baselines())
    await mind.open()
    assert mind.uris == [] and client.subscribes == []
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await anext(mind.pushes())
    assert client.listened == []


@pytest.mark.asyncio
async def test_poll_reports_best_clearance_across_needs():
    client = StubClient({
        "bread": [{"bread": [Match("a", {}, 0.80), Match("b", {}, 0.90)]}],
        "omens": [{"omens": [Match("a", {}, 0.95)]}],
    })
    mind = await opened(client, ("bread", "omens"), bread=0.70, omens=0.80)
    got = {x.data_key: x.clearance for x in await mind.poll()}
    assert got == {"a": pytest.approx(0.10), "b": pytest.approx(0.15)}
