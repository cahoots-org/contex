import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match, SubscriptionGone
from examples.rumor_mill.mind import Mind, mind_project
from examples.rumor_mill.models import Villager


class StubClient:
    def __init__(self, bundles, gone_once=False):
        self.bundles = list(bundles)
        self.gone_once = gone_once
        self.subscribes = []
        self.published = []

    async def subscribe(self, project_id, needs, **kwargs):
        self.subscribes.append((project_id, needs, kwargs))
        return "contex://subscriptions/s1"

    async def read(self, uri):
        if self.gone_once:
            self.gone_once = False
            raise SubscriptionGone(uri)
        return self.bundles.pop(0) if len(self.bundles) > 1 else self.bundles[0]

    async def publish(self, project_id, data_key, data):
        self.published.append((project_id, data_key, data))


def villager():
    return Villager("v1", "Ann", "baker", "kind", "none", ["bread prices"], {})


def m(key):
    return Match(key, {"text": key})


async def opened(client):
    mind = Mind(client, villager(), Config(day_seconds=100, memory_days=2))
    await mind.open()
    return mind


@pytest.mark.asyncio
async def test_poll_returns_only_new_matches():
    mind = await opened(StubClient([{"n": [m("a")]}, {"n": [m("a"), m("b")]}]))
    assert [x.data_key for x in await mind.poll()] == ["a"]
    assert [x.data_key for x in await mind.poll()] == ["b"]


@pytest.mark.asyncio
async def test_poll_resubscribes_after_subscription_gone():
    client = StubClient([{"n": [m("a")]}], gone_once=True)
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
    config = Config(day_seconds=100, memory_days=2, top_k=7, threshold=0.4)
    await Mind(client, villager(), config).open()
    project, needs, kwargs = client.subscribes[0]
    assert project == mind_project(config.session, "v1")
    assert needs == ["bread prices"]
    assert kwargs == {"max_age_seconds": 200, "top_k": 7, "threshold": 0.4}


@pytest.mark.asyncio
async def test_renew_does_not_swallow_unseen_matches():
    mind = await opened(StubClient([{"n": [m("a")]}]))
    await mind.renew()
    assert [x.data_key for x in await mind.poll()] == ["a"]


@pytest.mark.asyncio
async def test_renew_resubscribes_when_gone():
    client = StubClient([{"n": [m("a")]}], gone_once=True)
    mind = await opened(client)
    await mind.renew()
    assert len(client.subscribes) == 2
