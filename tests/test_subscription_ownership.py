"""Subscription tenant ownership enforcement tests (#42, #73)."""
import pytest

from src.core.subscriptions import SubscriptionService
from src.core.tenant import TenantManager


class _StubMatcher:
    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None, since=None):
        return {n: [{"data_key": "k", "similarity": 0.9, "data": {}, "description": "d"}] for n in needs}


@pytest.mark.asyncio
async def test_get_bundle_cross_tenant_raises(db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    await TenantManager(db).ensure_tenant("t1")
    await TenantManager(db).ensure_tenant("t2")

    svc = SubscriptionService(db, _StubMatcher())
    sub_id = await svc.create("p1", ["need1"], tenant_id="t2")

    with pytest.raises(PermissionError):
        await svc.get_bundle(sub_id, tenant_id="t1")


@pytest.mark.asyncio
async def test_get_bundle_same_tenant_returns_bundle(db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    await TenantManager(db).ensure_tenant("t1")

    svc = SubscriptionService(db, _StubMatcher())
    sub_id = await svc.create("p1", ["need1"], tenant_id="t1")

    bundle = await svc.get_bundle(sub_id, tenant_id="t1")
    assert "need1" in bundle


@pytest.mark.asyncio
async def test_multi_tenant_disabled_cross_tenant_is_noop(db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    await TenantManager(db).ensure_tenant("t1")
    await TenantManager(db).ensure_tenant("t2")

    svc = SubscriptionService(db, _StubMatcher())
    sub_id = await svc.create("p1", ["need1"], tenant_id="t2")

    bundle = await svc.get_bundle(sub_id, tenant_id="t1")
    assert "need1" in bundle
