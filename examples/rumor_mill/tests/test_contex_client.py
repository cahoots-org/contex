import json
from contextlib import asynccontextmanager

import pytest

from examples.rumor_mill import contex_client
from examples.rumor_mill.contex_client import ContexClient, Match, new_matches


def test_new_matches_dedupes_across_needs_and_skips_seen():
    a, b, c = Match("a", {"t": 1}), Match("b", {"t": 2}), Match("c", {"t": 3})
    bundle = {"n1": [a, b], "n2": [b, c]}
    assert new_matches({"a"}, bundle) == [b, c]
    assert new_matches(set(), {}) == []


class FakeSession:
    def __init__(self, url):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    @asynccontextmanager
    async def listen(self, resource_subscriptions):
        async def events():
            for _ in range(2):
                yield object()

        yield events()


@pytest.mark.asyncio
async def test_listen_yields_once_on_connect_then_per_push(monkeypatch):
    monkeypatch.setattr(contex_client, "Client", FakeSession)
    assert len([_ async for _ in ContexClient("url").listen("uri")]) == 3


@pytest.mark.asyncio
async def test_listen_many_yields_on_connect_and_per_push(monkeypatch):
    uris = []

    class Recording(FakeSession):
        @asynccontextmanager
        async def listen(self, resource_subscriptions):
            uris.append(resource_subscriptions)
            async with super().listen(resource_subscriptions) as events:
                yield events

    monkeypatch.setattr(contex_client, "Client", Recording)
    assert len([_ async for _ in ContexClient("url").listen_many(["a", "b"])]) == 3
    assert uris == [["a", "b"]]


@pytest.mark.asyncio
async def test_read_keeps_similarity():
    class Result:
        contents = [type("C", (), {"text": json.dumps({"n": [
            {"data_key": "a", "data": {}, "similarity": 0.8}, {"data_key": "b", "data": {}}]})})]

    class Fake:
        async def read_resource(self, uri):
            return Result

    client = ContexClient("url")
    client._client = Fake()
    bundle = await client.read("uri")
    assert [m.similarity for m in bundle["n"]] == [0.8, None]


@pytest.mark.asyncio
async def test_query_keeps_similarity_and_sends_since_only_when_set():
    sent = []

    async def tool(name, args):
        sent.append(args)
        return {"matches": [{"data_key": "a", "data": {}, "similarity": 0.7}]}

    client = ContexClient("url")
    client._tool = tool
    found = await client.query("p", "q")
    await client.query("p", "q", since="2026-01-01T00:00:00+00:00")
    assert found == [Match("a", {}, 0.7)]
    assert "since" not in sent[0]
    assert sent[1]["since"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_query_sends_threshold_only_when_set():
    sent = []

    async def tool(name, args):
        sent.append(args)
        return {"matches": []}

    client = ContexClient("url")
    client._tool = tool
    await client.query("p", "q")
    await client.query("p", "q", threshold=0.6)
    assert "threshold" not in sent[0]
    assert sent[1]["threshold"] == 0.6


@pytest.mark.asyncio
async def test_query_sends_max_age_only_when_set():
    sent = []

    async def tool(name, args):
        sent.append(args)
        return {"matches": []}

    client = ContexClient("url")
    client._tool = tool
    await client.query("p", "q")
    await client.query("p", "q", max_age_seconds=240)
    assert "max_age_seconds" not in sent[0]
    assert sent[1]["max_age_seconds"] == 240
