import pytest

from src.core.db_models import Symbol
from src.core.subscriptions import SubscriptionService


class _RecordingMatcher:
    """Fixed per-need bundles; records which needs were matched."""
    def __init__(self, bundles):
        self.bundles = bundles
        self.calls = []

    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None, since=None):
        self.calls.append(tuple(needs))
        return {n: [dict(m) for m in self.bundles.get(n, [])] for n in needs}


def _m(node, doc, sim=0.9):
    return {"data_key": node, "document": doc, "similarity": sim, "data": {}, "description": "d", "related": []}


async def _service(db):
    matcher = _RecordingMatcher({"a": [_m("a.py.f", "a.py")], "b": [_m("b.py.g", "b.py")]})
    svc = SubscriptionService(db, matcher)
    sub_a = await svc.create("p1", ["a"])
    sub_b = await svc.create("p1", ["b"])
    return svc, sub_a, sub_b


@pytest.mark.asyncio
async def test_ids_including_matches_bundled_documents(db):
    svc, sub_a, _ = await _service(db)
    assert await svc._ids_including("p1", {"a.py", "zzz"}) == {sub_a}
    assert await svc._ids_including("p2", {"a.py"}) == set()


@pytest.mark.asyncio
async def test_ids_linking_finds_refs_to_new_defs(db):
    svc, sub_a, _ = await _service(db)
    async with db.session() as s:
        s.add_all([
            Symbol(project_id="p1", data_key="a.py", node_key="a.py.f", name="helper", role="ref"),
            Symbol(project_id="p1", data_key="h.py", node_key="h.py.helper", name="helper", role="def"),
        ])
    assert await svc._ids_linking("p1", {"h.py"}) == {sub_a}
    assert await svc._ids_linking("p1", {"other.py"}) == set()


@pytest.mark.asyncio
async def test_affected_is_none_without_vector_check(db):
    svc, _, _ = await _service(db)
    assert await svc._affected("p1", {"a.py"}) is None


@pytest.mark.asyncio
async def test_reconcile_with_keys_only_touches_affected(db):
    svc, sub_a, _ = await _service(db)

    async def no_vector_hits(project_id, keys):
        return set()
    svc._ids_admitting = no_vector_hits
    svc.matcher.calls.clear()

    await svc.reconcile_project("p1", {"a.py"})

    assert svc.matcher.calls == [("a",)]


@pytest.mark.asyncio
async def test_reconcile_falls_back_to_all_when_filter_raises(db):
    svc, _, _ = await _service(db)

    async def broken(project_id, keys):
        raise RuntimeError("boom")
    svc._ids_including = broken
    svc.matcher.calls.clear()

    await svc.reconcile_project("p1", {"a.py"})

    assert sorted(svc.matcher.calls) == [("a",), ("b",)]


@pytest.mark.asyncio
async def test_reconcile_skips_subscription_deleted_mid_pass(db, caplog):
    svc, sub_a, sub_b = await _service(db)
    svc.matcher.bundles["a"] = [_m("a.py.f", "a.py", sim=0.1)]
    svc.matcher.bundles["b"] = [_m("b.py.g", "b.py", sim=0.1)]
    real_match = svc.matcher.match

    async def match_then_delete(project_id, needs, **kw):
        result = await real_match(project_id, needs, **kw)
        if needs == ["a"]:
            await svc.delete(sub_a)
        return result
    svc.matcher.match = match_then_delete

    changed = await svc.reconcile_project("p1")

    assert changed == [sub_b]
    assert "reconcile failed" not in caplog.text
