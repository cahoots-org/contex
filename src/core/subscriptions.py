"""Persistent semantic subscriptions with materialized bundles + reconcile."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from src.core.db_models import Embedding, Subscription, Symbol
from src.core.limits import check_needs, clamp_top_k, positive_int_env
from src.core.tenant import DEFAULT_TENANT_ID
from src.core.authz import auth_enabled
from src.core.notifier import SUBSCRIPTION_UPDATED
from src.core.recency import window_start

logger = logging.getLogger(__name__)

# Cap cross-file neighbors attached per matched node, to bound bundle size.
# ponytail: fixed cap; make it configurable only if real bundles feel starved.
MAX_LINKS_PER_NODE = 5

# How long a subscription outlives its last create or read.
SUBSCRIPTION_TTL_SECONDS = positive_int_env("SUBSCRIPTION_TTL_SECONDS", 3600)

# Per (subscription, need): documents held and the weakest one's similarity.
_FLOORS_SQL = text("""
SELECT s.subscription_id, n.need,
       COALESCE(s.top_k, :default_top_k) AS top_k,
       COALESCE(s.threshold, :default_threshold) AS threshold,
       jsonb_array_length(COALESCE(s.bundle -> n.need, '[]'::jsonb)) AS docs,
       (SELECT min((m->>'similarity')::float)
          FROM jsonb_array_elements(COALESCE(s.bundle -> n.need, '[]'::jsonb)) m) AS min_sim
  FROM subscriptions s, unnest(s.needs) AS n(need)
 WHERE s.project_id = :project_id
""")


def _since_from_scope(scope):
    """The recency cutoff from ``scope["since"]`` (ISO-8601) and ``scope["max_age_seconds"]``."""
    if not scope:
        return None
    raw = scope.get("since")
    return window_start(datetime.fromisoformat(raw) if raw else None, scope.get("max_age_seconds"))


def _assert_sub_tenant(row, tenant_id):
    if auth_enabled() and tenant_id is not None and row.tenant_id != tenant_id:
        raise PermissionError("Permission denied")


def _content_id(tenant_id, project_id, needs, top_k, threshold, scope) -> str:
    """Same tenant, project, needs and params -> same subscription."""
    key = json.dumps([tenant_id, project_id, needs, top_k, threshold, scope], sort_keys=True)
    return f"sub_{hashlib.sha256(key.encode()).hexdigest()[:32]}"


async def _notify_updated(session, subscription_id, at: datetime) -> None:
    """Queue a `subscription updated` event; Postgres delivers it only on commit."""
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": SUBSCRIPTION_UPDATED,
         "payload": json.dumps({"subscription_id": subscription_id, "updated_at": at.isoformat()})},
    )


def _expired(row) -> bool:
    return row.expires_at <= datetime.now(timezone.utc)


def _bundle_documents(bundle) -> list[str]:
    """Sorted document keys of every match and link in a bundle."""
    docs = set()
    for matches in bundle.values():
        for m in matches:
            docs.add(m.get("document") or m["data_key"])
            docs.update(link.get("document") or link["data_key"] for link in m.get("links", ()))
    return sorted(docs)


class SubscriptionService:
    def __init__(
        self, db, matcher, encoder=None, default_threshold=0.35, default_top_k=10,
        ttl_seconds=SUBSCRIPTION_TTL_SECONDS,
    ) -> None:
        self.db = db
        self.ttl = timedelta(seconds=ttl_seconds)
        self.matcher = matcher
        self.encoder = encoder
        self.default_threshold = default_threshold
        self.default_top_k = default_top_k
        # ponytail: unbounded, ~3KB per distinct need; persist vectors behind an ANN index if this binds.
        self._need_vecs: dict[str, np.ndarray] = {}

    async def create(
        self, project_id, needs, tenant_id=DEFAULT_TENANT_ID, scope=None, subscription_id=None, top_k=None, threshold=None
    ) -> str:
        """Subscribe to `needs`, returning the subscription id.

        Identical requests share one subscription: a live one is renewed and
        returned as is, so a reconnecting agent gets its bundle back without a
        re-match. Pass `subscription_id` for a private subscription instead.
        """
        check_needs(needs)
        needs = sorted(set(needs))
        tenant_id = tenant_id or DEFAULT_TENANT_ID
        top_k = clamp_top_k(top_k)
        sub_id = subscription_id or _content_id(tenant_id, project_id, needs, top_k, threshold, scope)
        if await self._renew(sub_id):
            return sub_id

        since = _since_from_scope(scope)
        bundle = await self.matcher.match(project_id, needs, top_k=top_k, threshold=threshold, since=since)
        bundle = await self._link_bundle(project_id, bundle)
        now = datetime.now(timezone.utc)
        fresh = dict(
            bundle=bundle, documents=_bundle_documents(bundle),
            bundle_updated_at=now, expires_at=now + self.ttl,
        )
        async with self.db.session() as session:
            # Conflict: a concurrent create won the race, or an expired row awaits the purge.
            await session.execute(
                insert(Subscription).values(
                    subscription_id=sub_id, project_id=project_id, tenant_id=tenant_id,
                    needs=needs, scope=scope, top_k=top_k, threshold=threshold, **fresh,
                ).on_conflict_do_update(index_elements=[Subscription.subscription_id], set_=fresh)
            )
            await session.commit()
        return sub_id

    async def _renew(self, subscription_id) -> bool:
        """Extend a live subscription's lease; False if it is missing or expired."""
        async with self.db.session() as session:
            renewed = (await session.execute(
                update(Subscription)
                .where(Subscription.subscription_id == subscription_id)
                .where(Subscription.expires_at > func.now())
                .values(expires_at=datetime.now(timezone.utc) + self.ttl)
                .returning(Subscription.subscription_id)
            )).scalar_one_or_none()
            await session.commit()
        return renewed is not None

    async def get_bundle(self, subscription_id, *, tenant_id=None) -> dict:
        """The subscription's bundle; reading it renews the lease."""
        async with self.db.session() as session:
            row = (await session.execute(
                select(Subscription).where(Subscription.subscription_id == subscription_id)
            )).scalar_one_or_none()
            if row is None or _expired(row):
                raise KeyError(subscription_id)
            _assert_sub_tenant(row, tenant_id)
            row.expires_at = datetime.now(timezone.utc) + self.ttl
            await session.commit()
            return row.bundle

    async def all_ids(self) -> list[str]:
        async with self.db.session() as session:
            return list((await session.execute(select(Subscription.subscription_id))).scalars())

    async def project_of(self, subscription_id, *, tenant_id=None) -> str | None:
        """The subscription's project, or None if it does not exist."""
        async with self.db.session() as session:
            row = (await session.execute(
                select(Subscription).where(Subscription.subscription_id == subscription_id)
            )).scalar_one_or_none()
            if row is None or _expired(row):
                return None
            _assert_sub_tenant(row, tenant_id)
            return row.project_id

    async def delete(self, subscription_id, *, tenant_id=None) -> None:
        async with self.db.session() as session:
            row = (await session.execute(
                select(Subscription).where(Subscription.subscription_id == subscription_id)
            )).scalar_one_or_none()
            # Idempotent: only commit when a row actually existed; absent id is a no-op.
            if row is not None:
                _assert_sub_tenant(row, tenant_id)
                await session.delete(row)
                # Agents sharing it re-read, find it gone, and can re-create it.
                await _notify_updated(session, subscription_id, datetime.now(timezone.utc))
                await session.commit()

    async def reconcile_project(self, project_id, changed_keys: set[str] | None = None) -> list[str]:
        """Bring every subscription in a project back in sync with current data.

        Re-matches each subscription's needs against the project's current data and,
        for any whose materialized bundle changed, atomically swaps the stored bundle
        (buffer-until-complete) and emits a `subscription:{id}:updated` event. Returns
        the list of subscription ids that changed. `changed_keys` names the documents
        just published; `None` reconciles every subscription.
        """
        affected = None
        if changed_keys is not None:
            try:
                affected = await self._affected(project_id, changed_keys)
            except Exception:
                logger.exception("affected-subscription filter failed for %s; reconciling all", project_id)

        stmt = (
            select(Subscription)
            .where(Subscription.project_id == project_id)
            .where(Subscription.expires_at > func.now())
        )
        if affected is not None:
            stmt = stmt.where(Subscription.subscription_id.in_(affected))
        async with self.db.session() as session:
            subs = (await session.execute(stmt)).scalars().all()

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
                    )).scalar_one_or_none()
                    if row is None:  # deleted since the filter ran
                        continue
                    row.bundle = new_bundle
                    row.documents = _bundle_documents(new_bundle)
                    row.bundle_updated_at = now
                    # Delivered only on commit, so listeners never see an unreadable bundle.
                    await _notify_updated(session, sub.subscription_id, now)
                    await session.commit()
                changed_ids.append(sub.subscription_id)
            except Exception:
                logger.exception("reconcile failed for subscription %s", sub.subscription_id)
                continue
        return changed_ids

    async def _ids_including(self, project_id, keys) -> set[str]:
        """Subscriptions whose bundle already touches a changed document."""
        async with self.db.session() as session:
            return set((await session.execute(
                select(Subscription.subscription_id)
                .where(Subscription.project_id == project_id)
                .where(Subscription.documents.overlap(list(keys)))
            )).scalars())

    async def _ids_linking(self, project_id, keys) -> set[str]:
        """Subscriptions bundling a document that references a name a changed document defines."""
        defined = (
            select(Symbol.name)
            .where(Symbol.project_id == project_id)
            .where(Symbol.data_key.in_(keys))
            .where(Symbol.role == "def")
        )
        async with self.db.session() as session:
            referencing = list((await session.execute(
                select(Symbol.data_key).distinct()
                .where(Symbol.project_id == project_id)
                .where(Symbol.role == "ref")
                .where(Symbol.name.in_(defined))
            )).scalars())
        if not referencing:
            return set()
        return await self._ids_including(project_id, set(referencing))

    async def _floors(self, project_id) -> list[tuple[str, str, float]]:
        """(subscription_id, need, floor): the cosine a new node must reach to enter."""
        async with self.db.session() as session:
            rows = (await session.execute(_FLOORS_SQL, {
                "project_id": project_id,
                "default_top_k": self.default_top_k,
                "default_threshold": self.default_threshold,
            })).all()
        return [
            (r.subscription_id, r.need,
             r.threshold if r.docs < r.top_k or r.min_sim is None else max(r.threshold, r.min_sim))
            for r in rows
        ]

    def _need_vectors(self, needs) -> np.ndarray:
        missing = sorted({n for n in needs if n not in self._need_vecs})
        if missing:
            self._need_vecs.update(zip(missing, self.encoder.encode(missing)))
        return np.stack([self._need_vecs[n] for n in needs])

    async def _ids_admitting(self, project_id, keys) -> set[str] | None:
        """Subscriptions with a need some changed node reaches the floor of; None when unknown."""
        if self.encoder is None:
            return None
        async with self.db.session() as session:
            nodes = (await session.execute(
                select(Embedding.embedding)
                .where(Embedding.project_id == project_id)
                .where(Embedding.data_key.in_(keys))
            )).scalars().all()
        floors = await self._floors(project_id)
        if not nodes or not floors:
            return set()
        best = (np.stack(nodes) @ self._need_vectors([need for _, need, _ in floors]).T).max(axis=0)
        return {sub for (sub, _, floor), score in zip(floors, best) if score >= floor}

    async def _affected(self, project_id, keys) -> set[str] | None:
        """Subscriptions a publish of ``keys`` can change; None means all of them."""
        admitting = await self._ids_admitting(project_id, keys)
        if admitting is None:
            return None
        return (
            admitting
            | await self._ids_including(project_id, keys)
            | await self._ids_linking(project_id, keys)
        )

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
                            "data_key": dk, "document": e.data_key, "name": name,
                            "data": e.data, "description": e.description,
                        })
                if links:
                    m["links"] = links[:MAX_LINKS_PER_NODE]
        return bundle
