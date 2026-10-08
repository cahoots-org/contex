"""Periodic full reconcile: catches matches the publish-time filter misses."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text

from src.core.db_models import Subscription

logger = logging.getLogger(__name__)

# Arbitrary constant key shared by every replica.
_LOCK_KEY = 0x636F6E746578


async def sweep_once(db, subscriptions) -> bool:
    """Reconcile every project with subscriptions. False if another replica holds the sweep."""
    async with db.session() as lock_session:
        if not await lock_session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _LOCK_KEY}):
            return False
        projects = (await lock_session.execute(
            select(Subscription.project_id).distinct()
        )).scalars().all()
        for project_id in projects:
            try:
                await subscriptions.reconcile_project(project_id)
            except Exception:
                logger.exception("sweep reconcile failed for %s", project_id)
    return True


async def run_sweep(db, subscriptions, interval: float) -> None:
    """Sweep every `interval` seconds until cancelled."""
    while True:
        await asyncio.sleep(interval)
        try:
            await sweep_once(db, subscriptions)
        except Exception:
            logger.exception("reconcile sweep failed")
