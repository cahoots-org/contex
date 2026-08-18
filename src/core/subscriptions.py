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

    async def delete(self, subscription_id) -> None:
        async with self.db.session() as session:
            row = (await session.execute(
                select(Subscription).where(Subscription.subscription_id == subscription_id)
            )).scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def reconcile_project(self, project_id, changed_data_key=None) -> list[str]:
        async with self.db.session() as session:
            subs = (await session.execute(
                select(Subscription).where(Subscription.project_id == project_id)
            )).scalars().all()

        changed_ids: list[str] = []
        for sub in subs:
            new_bundle = await self.matcher.match(project_id, sub.needs)  # computed fully first
            if new_bundle == sub.bundle:
                continue
            now = datetime.now(timezone.utc)  # single timestamp for both DB + event
            async with self.db.session() as session:  # buffer-until-complete: one atomic swap
                row = (await session.execute(
                    select(Subscription).where(Subscription.subscription_id == sub.subscription_id)
                )).scalar_one()
                row.bundle = new_bundle
                row.bundle_updated_at = now
                await session.commit()  # commit BEFORE publish: reader must see committed value
            await self.redis.publish(
                f"subscription:{sub.subscription_id}:updated",
                json.dumps({
                    "subscription_id": sub.subscription_id,
                    "updated_at": now.isoformat(),
                }),
            )
            changed_ids.append(sub.subscription_id)
        return changed_ids
