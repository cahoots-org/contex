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
