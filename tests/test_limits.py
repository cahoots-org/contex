"""Defense-in-depth input ceilings (src/core/limits.py)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from src.core import subscriptions
from src.core.db_models import Subscription

from src.core.limits import (
    MAX_BATCH_SIZE,
    MAX_EVENT_COUNT,
    MAX_NEEDS,
    MAX_TOP_K,
    check_batch_size,
    check_needs,
    clamp_count,
    clamp_top_k,
)
from src.core.subscriptions import SubscriptionService


def test_clamp_top_k_bounds():
    assert clamp_top_k(None) is None          # caller default preserved
    assert clamp_top_k(5) == 5
    assert clamp_top_k(0) == 1                 # floor
    assert clamp_top_k(-10) == 1
    assert clamp_top_k(10**9) == MAX_TOP_K     # ceiling
    assert clamp_top_k(MAX_TOP_K) == MAX_TOP_K


def test_clamp_count_bounds():
    assert clamp_count(None) == MAX_EVENT_COUNT   # unbounded read is never unlimited
    assert clamp_count(50) == 50
    assert clamp_count(0) == 1
    assert clamp_count(10**9) == MAX_EVENT_COUNT


def test_check_needs_rejects_oversized():
    check_needs(["a"] * MAX_NEEDS)             # at the cap is fine
    with pytest.raises(ValueError, match="Too many needs"):
        check_needs(["a"] * (MAX_NEEDS + 1))


def test_check_batch_size_rejects_oversized():
    check_batch_size(list(range(MAX_BATCH_SIZE)), "events")
    with pytest.raises(ValueError, match="Batch too large"):
        check_batch_size(list(range(MAX_BATCH_SIZE + 1)), "events")


@pytest.mark.asyncio
async def test_subscription_rejects_too_many_needs():
    # The needs cap must trip before any DB or matcher work, so a service wired
    # with no dependencies still raises on an oversized request.
    svc = SubscriptionService(db=None, matcher=None)
    with pytest.raises(ValueError, match="Too many needs"):
        await svc.create("proj", ["need"] * (MAX_NEEDS + 1))


class _EmptyMatcher:
    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None, since=None):
        return {n: [] for n in needs}


@pytest.mark.asyncio
async def test_subscriptions_per_project_are_capped(db, monkeypatch):
    monkeypatch.setattr(subscriptions, "MAX_SUBSCRIPTIONS_PER_PROJECT", 2)
    svc = SubscriptionService(db, _EmptyMatcher())
    first = await svc.create("proj", ["a"])
    await svc.create("proj", ["b"])
    await svc.create("other", ["c"])           # other projects don't count

    with pytest.raises(ValueError, match="Too many subscriptions"):
        await svc.create("proj", ["d"])
    assert await svc.create("proj", ["a"]) == first   # renewing an existing one is fine


@pytest.mark.asyncio
async def test_expired_subscriptions_do_not_count_toward_the_cap(db, monkeypatch):
    monkeypatch.setattr(subscriptions, "MAX_SUBSCRIPTIONS_PER_PROJECT", 1)
    svc = SubscriptionService(db, _EmptyMatcher())
    sub = await svc.create("proj", ["a"])
    async with db.session() as session:
        await session.execute(
            update(Subscription).where(Subscription.subscription_id == sub)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await session.commit()

    await svc.create("proj", ["b"])
