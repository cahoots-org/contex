"""Persistent semantic subscriptions with materialized bundles + reconcile."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from src.core.db_models import Subscription


class SubscriptionService:
    def __init__(self, db, matcher, redis) -> None:
        self.db = db
        self.matcher = matcher
        self.redis = redis

    async def create(
        self, project_id, needs, tenant_id=None, scope=None, subscription_id=None
    ) -> str:
        sub_id = subscription_id or f"sub_{uuid4().hex}"
        bundle = await self.matcher.match(project_id, needs)
        async with self.db.session() as session:
            session.add(Subscription(
                subscription_id=sub_id, project_id=project_id, tenant_id=tenant_id,
                needs=list(needs), scope=scope, bundle=bundle,
                bundle_updated_at=datetime.now(timezone.utc),
            ))
            await session.commit()
        return sub_id

    async def get_bundle(self, subscription_id) -> dict:
        async with self.db.session() as session:
            row = (await session.execute(
                select(Subscription).where(Subscription.subscription_id == subscription_id)
            )).scalar_one_or_none()
            if row is None:
                raise KeyError(subscription_id)
            return row.bundle
