"""run(prune=True) deletes what the source no longer has (#259)."""
from __future__ import annotations

import asyncio

import pytest

from connectors.base import ChangeEvent
from connectors.base.runner import run
from connectors.base.secrets import SecretScanner


class _Publisher:
    """Holds an origin's keys like the server would."""

    def __init__(self, existing: list[str]) -> None:
        self.existing = existing
        self.published: list[str] = []
        self.deleted: list[str] = []
        self.listed = False

    async def publish_batch(self, items: list[dict]) -> int:
        self.published += [i["data_key"] for i in items]
        return len(items)

    async def delete_batch(self, data_keys: list[str]) -> int:
        self.deleted += data_keys
        return len(data_keys)

    async def list_keys(self) -> list[str]:
        self.listed = True
        return self.existing


def _upsert(key: str, payload: str = "x") -> ChangeEvent:
    return ChangeEvent(op="upsert", key=key, payload=payload, data_format="text")


def test_prune_deletes_keys_the_run_did_not_see():
    pub = _Publisher(["a", "b", "gone"])
    stats = asyncio.run(run([_upsert("a"), _upsert("b")], pub, prune=True))
    assert pub.deleted == ["gone"]
    assert stats.deleted == 1


def test_no_prune_by_default():
    pub = _Publisher(["a", "gone"])
    asyncio.run(run([_upsert("a")], pub))
    assert pub.deleted == []
    assert not pub.listed


def test_retained_keys_survive_the_prune():
    pub = _Publisher(["a", "flaky"])
    asyncio.run(run([_upsert("a"), ChangeEvent(op="retain", key="flaky", payload=None)], pub, prune=True))
    assert pub.deleted == []
    assert pub.published == ["a"]


def test_items_now_dropped_as_secrets_are_pruned():
    pub = _Publisher(["a", ".env"])
    asyncio.run(run([_upsert("a"), _upsert(".env", "TOKEN=1")], pub, prune=True,
                    secret_scanner=SecretScanner()))
    assert pub.deleted == [".env"]


def test_failed_run_never_prunes():
    async def events():
        yield _upsert("a")
        raise RuntimeError("source went away")

    pub = _Publisher(["a", "b"])
    with pytest.raises(RuntimeError):
        asyncio.run(run(events(), pub, prune=True))
    assert pub.deleted == []
    assert not pub.listed


def test_prune_deletes_in_batches():
    pub = _Publisher(["k1", "k2", "k3"])
    calls: list[int] = []
    delete = pub.delete_batch

    async def recording_delete(keys):
        calls.append(len(keys))
        return await delete(keys)

    pub.delete_batch = recording_delete
    asyncio.run(run([], pub, batch_size=2, prune=True))
    assert calls == [2, 1]
