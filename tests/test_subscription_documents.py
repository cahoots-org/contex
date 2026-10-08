import importlib.util
import pathlib

import pytest
from sqlalchemy import select, text, update

from src.core.db_models import Embedding, Subscription
from src.core.subscriptions import SubscriptionService, _bundle_documents

_spec = importlib.util.spec_from_file_location(
    "m016", pathlib.Path(__file__).parent.parent / "alembic/versions/016_subscription_documents.py"
)
m016 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m016)


class _KeyedMatcher:
    def __init__(self, bundles):
        self.bundles = bundles

    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None, since=None):
        return {n: list(self.bundles.get(n, [])) for n in needs}


def _m(node, doc, sim=0.9, links=()):
    entry = {"data_key": node, "document": doc, "similarity": sim, "data": {}, "description": "d", "related": []}
    if links:
        entry["links"] = list(links)
    return entry


def test_bundle_documents_collects_matches_and_links():
    bundle = {
        "a": [_m("x.py.f", "x.py", links=[{"data_key": "y.py.g", "document": "y.py", "name": "g"}])],
        "b": [_m("z.md#h", "z.md"), {"data_key": "legacy", "similarity": 0.5}],
    }
    assert _bundle_documents(bundle) == ["legacy", "x.py", "y.py", "z.md"]


async def _documents(db, sub_id):
    async with db.session() as s:
        return (await s.execute(
            select(Subscription.documents).where(Subscription.subscription_id == sub_id)
        )).scalar_one()


@pytest.mark.asyncio
async def test_create_and_reconcile_write_documents(db):
    m = _KeyedMatcher({"auth": [_m("a.py.f", "a.py")]})
    svc = SubscriptionService(db, m)
    sub_id = await svc.create("p1", ["auth"])
    assert await _documents(db, sub_id) == ["a.py"]

    m.bundles["auth"] = [_m("b.py.g", "b.py")]
    await svc.reconcile_project("p1")
    assert await _documents(db, sub_id) == ["b.py"]


@pytest.mark.asyncio
async def test_documents_overlap_is_indexable(db):
    svc = SubscriptionService(db, _KeyedMatcher({"auth": [_m("a.py.f", "a.py")]}))
    sub_id = await svc.create("p1", ["auth"])
    async with db.session() as s:
        hits = (await s.execute(
            select(Subscription.subscription_id).where(Subscription.documents.overlap(["a.py", "q"]))
        )).scalars().all()
        index = await s.scalar(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_subscriptions_documents'"
        ))
    assert hits == [sub_id]
    assert "gin" in index.lower()


@pytest.mark.asyncio
async def test_migration_backfill_sql_derives_documents(db):
    svc = SubscriptionService(db, _KeyedMatcher({"auth": [_m("a.py.f", "a.py", links=[{"data_key": "b.py.g", "document": "b.py", "name": "g"}])]}))
    sub_id = await svc.create("p1", ["auth"])
    async with db.session() as s:
        await s.execute(text("UPDATE subscriptions SET documents = '{}'"))
        await s.execute(text(m016.BACKFILL_SQL))
    assert await _documents(db, sub_id) == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_migration_backfill_resolves_pre_016_links_through_embeddings(db):
    svc = SubscriptionService(db, _KeyedMatcher({"auth": [_m("a.py.f", "a.py")]}))
    sub_id = await svc.create("p1", ["auth"])
    old_bundle = {"auth": [_m("a.py.f", "a.py", links=[{"data_key": "b.py.g", "name": "g"}])]}
    async with db.session() as s:
        s.add(Embedding(project_id="p1", data_key="b.py", node_key="b.py.g", data={}, embedding=[0.0] * 768))
        await s.flush()
        await s.execute(
            update(Subscription).where(Subscription.subscription_id == sub_id).values(bundle=old_bundle, documents=[])
        )
        await s.execute(text(m016.BACKFILL_SQL))
    assert await _documents(db, sub_id) == ["a.py", "b.py"]
