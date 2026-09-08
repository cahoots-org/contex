"""Subscription tenant ownership enforcement tests (#42, #73)."""
import pytest
from sqlalchemy import select, text

from src.core.db_models import Subscription
from src.core.subscriptions import SubscriptionService
from src.core.tenant import DEFAULT_TENANT_ID


class _StubMatcher:
    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None):
        return {n: [{"data_key": "k", "similarity": 0.9, "data": {}, "description": "d"}] for n in needs}


async def _ensure_tenant(db, tenant_id):
    async with db.session() as session:
        exists = (await session.execute(
            text("SELECT 1 FROM tenants WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )).scalar_one_or_none()
        if not exists:
            await session.execute(
                text("INSERT INTO tenants (tenant_id, name, is_active) VALUES (:tid, :name, true)"),
                {"tid": tenant_id, "name": tenant_id},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_get_bundle_cross_tenant_raises(db, redis, monkeypatch):
    monkeypatch.setattr("src.core.subscriptions.MULTI_TENANT_ENABLED", True)
    await _ensure_tenant(db, "t1")
    await _ensure_tenant(db, "t2")

    svc = SubscriptionService(db, _StubMatcher(), redis)
    sub_id = await svc.create("p1", ["need1"], tenant_id="t2")

    with pytest.raises(PermissionError):
        await svc.get_bundle(sub_id, tenant_id="t1")


@pytest.mark.asyncio
async def test_get_bundle_same_tenant_returns_bundle(db, redis, monkeypatch):
    monkeypatch.setattr("src.core.subscriptions.MULTI_TENANT_ENABLED", True)
    await _ensure_tenant(db, "t1")

    svc = SubscriptionService(db, _StubMatcher(), redis)
    sub_id = await svc.create("p1", ["need1"], tenant_id="t1")

    bundle = await svc.get_bundle(sub_id, tenant_id="t1")
    assert "need1" in bundle


@pytest.mark.asyncio
async def test_delete_cross_tenant_raises_and_sub_remains(db, redis, monkeypatch):
    monkeypatch.setattr("src.core.subscriptions.MULTI_TENANT_ENABLED", True)
    await _ensure_tenant(db, "t1")
    await _ensure_tenant(db, "t2")

    svc = SubscriptionService(db, _StubMatcher(), redis)
    sub_id = await svc.create("p1", ["need1"], tenant_id="t2")

    with pytest.raises(PermissionError):
        await svc.delete(sub_id, tenant_id="t1")

    async with db.session() as session:
        row = (await session.execute(
            select(Subscription).where(Subscription.subscription_id == sub_id)
        )).scalar_one_or_none()
    assert row is not None


@pytest.mark.asyncio
async def test_delete_same_tenant_succeeds(db, redis, monkeypatch):
    monkeypatch.setattr("src.core.subscriptions.MULTI_TENANT_ENABLED", True)
    await _ensure_tenant(db, "t1")

    svc = SubscriptionService(db, _StubMatcher(), redis)
    sub_id = await svc.create("p1", ["need1"], tenant_id="t1")

    await svc.delete(sub_id, tenant_id="t1")

    async with db.session() as session:
        row = (await session.execute(
            select(Subscription).where(Subscription.subscription_id == sub_id)
        )).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_multi_tenant_disabled_cross_tenant_is_noop(db, redis, monkeypatch):
    monkeypatch.setattr("src.core.subscriptions.MULTI_TENANT_ENABLED", False)
    await _ensure_tenant(db, "t1")
    await _ensure_tenant(db, "t2")

    svc = SubscriptionService(db, _StubMatcher(), redis)
    sub_id = await svc.create("p1", ["need1"], tenant_id="t2")

    bundle = await svc.get_bundle(sub_id, tenant_id="t1")
    assert "need1" in bundle

    await svc.delete(sub_id, tenant_id="t1")

    async with db.session() as session:
        row = (await session.execute(
            select(Subscription).where(Subscription.subscription_id == sub_id)
        )).scalar_one_or_none()
    assert row is None
