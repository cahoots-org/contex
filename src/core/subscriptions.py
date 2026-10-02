"""Persistent semantic subscriptions with materialized bundles + reconcile."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from src.core.db_models import Embedding, Subscription, Symbol
from src.core.limits import check_needs, clamp_top_k
from src.core.tenant import DEFAULT_TENANT_ID
from src.core.authz import auth_enabled

logger = logging.getLogger(__name__)

# Cap cross-file neighbors attached per matched node, to bound bundle size.
# ponytail: fixed cap; make it configurable only if real bundles feel starved.
MAX_LINKS_PER_NODE = 5


def _since_from_scope(scope):
    """Extract the recency cutoff (``scope["since"]``, ISO-8601) as a datetime."""
    if not scope:
        return None
    raw = scope.get("since")
    return datetime.fromisoformat(raw) if raw else None


def _assert_sub_tenant(row, tenant_id):
    if auth_enabled() and tenant_id is not None and row.tenant_id != tenant_id:
        raise PermissionError("Permission denied")


class SubscriptionService:
    def __init__(self, db, matcher, redis) -> None:
        self.db = db
        self.matcher = matcher
        self.redis = redis

    async def create(
        self, project_id, needs, tenant_id=DEFAULT_TENANT_ID, scope=None, subscription_id=None, top_k=None, threshold=None
    ) -> str:
        check_needs(needs)
        top_k = clamp_top_k(top_k)
        sub_id = subscription_id or f"sub_{uuid4().hex}"
        since = _since_from_scope(scope)
        bundle = await self.matcher.match(project_id, needs, top_k=top_k, threshold=threshold, since=since)
        bundle = await self._link_bundle(project_id, bundle)
        async with self.db.session() as session:
            session.add(Subscription(
                subscription_id=sub_id, project_id=project_id, tenant_id=tenant_id,
                needs=list(needs), scope=scope, top_k=top_k, threshold=threshold, bundle=bundle,
                bundle_updated_at=datetime.now(timezone.utc),
            ))
            await session.commit()
        return sub_id

    async def get_bundle(self, subscription_id, *, tenant_id=None) -> dict:
        async with self.db.session() as session:
            row = (await session.execute(
                select(Subscription).where(Subscription.subscription_id == subscription_id)
            )).scalar_one_or_none()
            if row is None:
                raise KeyError(subscription_id)
            _assert_sub_tenant(row, tenant_id)
            return row.bundle

    async def delete(self, subscription_id, *, tenant_id=None) -> None:
        async with self.db.session() as session:
            row = (await session.execute(
                select(Subscription).where(Subscription.subscription_id == subscription_id)
            )).scalar_one_or_none()
            # Idempotent: only commit when a row actually existed; absent id is a no-op.
            if row is not None:
                _assert_sub_tenant(row, tenant_id)
                await session.delete(row)
                await session.commit()

    async def reconcile_project(self, project_id, changed_data_key=None) -> list[str]:
        """Bring every subscription in a project back in sync with current data.

        Re-matches each subscription's needs against the project's current data and,
        for any whose materialized bundle changed, atomically swaps the stored bundle
        (buffer-until-complete) and emits a `subscription:{id}:updated` event. Returns
        the list of subscription ids that changed. `changed_data_key` is accepted for a
        future optimization (reconcile only subscriptions affected by that key); for now
        every subscription in the project is re-checked.
        """
        async with self.db.session() as session:
            subs = (await session.execute(
                select(Subscription).where(Subscription.project_id == project_id)
            )).scalars().all()

        changed_ids: list[str] = []
        for sub in subs:
            try:
                since = _since_from_scope(sub.scope)
                new_bundle = await self.matcher.match(project_id, sub.needs, top_k=sub.top_k, threshold=sub.threshold, since=since)  # computed fully first
                new_bundle = await self._link_bundle(project_id, new_bundle)
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
            except Exception:
                logger.exception("reconcile failed for subscription %s", sub.subscription_id)
                continue
        return changed_ids

    async def _link_bundle(self, project_id, bundle):
        """Attach cross-file neighbors to each matched node via the symbols join.

        The "edge" is a read-time equality join: for every matched node, resolve
        the names it references (role="ref") to the nodes that define them
        (role="def") elsewhere in the project, and attach those as ``links``.
        Running this at bundle-build time (create + every reconcile) solves ingest
        ordering for free — a ref whose def hasn't arrived yet simply doesn't
        resolve, and the def's later arrival triggers a reconcile that re-runs
        this join. Output is deterministic (sorted) so the reconcile equality
        check doesn't see spurious changes.
        """
        matched_keys = {m["data_key"] for ms in bundle.values() for m in ms}
        if not matched_keys:
            return bundle

        async with self.db.session() as session:
            ref_rows = (await session.execute(
                select(Symbol.node_key, Symbol.name)
                .where(Symbol.project_id == project_id)
                .where(Symbol.node_key.in_(matched_keys))
                .where(Symbol.role == "ref")
            )).all()
            if not ref_rows:
                return bundle
            ref_names = {name for _, name in ref_rows}

            def_rows = (await session.execute(
                select(Symbol.name, Symbol.node_key)
                .where(Symbol.project_id == project_id)
                .where(Symbol.name.in_(ref_names))
                .where(Symbol.role == "def")
            )).all()
            if not def_rows:
                return bundle
            defs_by_name: dict[str, set[str]] = {}
            for name, node_key in def_rows:
                defs_by_name.setdefault(name, set()).add(node_key)

            # All defining nodes are candidate neighbors — including ones that are
            # themselves matched (a matched handler should still link to a matched
            # function it calls). Self-links are excluded per-entry below.
            neighbor_keys = {nk for nks in defs_by_name.values() for nk in nks}
            if not neighbor_keys:
                return bundle
            emb_by_key = {
                e.node_key: e for e in (await session.execute(
                    select(Embedding)
                    .where(Embedding.project_id == project_id)
                    .where(Embedding.node_key.in_(neighbor_keys))
                )).scalars().all()
            }

        refs_by_node: dict[str, set[str]] = {}
        for node_key, name in ref_rows:
            refs_by_node.setdefault(node_key, set()).add(name)

        for ms in bundle.values():
            for m in ms:
                links, seen = [], set()
                for name in sorted(refs_by_node.get(m["data_key"], ())):
                    for dk in sorted(defs_by_name.get(name, ())):
                        if dk == m["data_key"] or dk in seen or dk not in emb_by_key:
                            continue
                        seen.add(dk)
                        e = emb_by_key[dk]
                        links.append({
                            "data_key": dk, "name": name,
                            "data": e.data, "description": e.description,
                        })
                if links:
                    m["links"] = links[:MAX_LINKS_PER_NODE]
        return bundle
