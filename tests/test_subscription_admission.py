import threading

import numpy as np
import pytest

from src.core.db_models import Embedding
from src.core.subscriptions import SubscriptionService


def _unit(i):
    v = np.zeros(768, dtype=np.float32)
    v[i] = 1.0
    return v


def _blend(i, j, w):
    """Unit vector at cosine ``w`` to axis i, the rest on axis j."""
    v = np.zeros(768, dtype=np.float32)
    v[i], v[j] = w, np.sqrt(1 - w * w)
    return v


class _AxisEncoder:
    """Need text 'axisN' encodes to the unit vector on axis N."""
    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        return np.stack([_unit(int(t.removeprefix("axis"))) for t in texts])


class _RecordingMatcher:
    def __init__(self, bundles):
        self.bundles = bundles
        self.calls = []

    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None, since=None):
        self.calls.append(tuple(needs))
        return {n: [dict(m) for m in self.bundles.get(n, [])] for n in needs}


def _m(doc, sim):
    return {"data_key": f"{doc}.n", "document": doc, "similarity": sim, "data": {}, "description": "d", "related": []}


async def _node(db, project, doc, vec):
    async with db.session() as s:
        s.add(Embedding(project_id=project, data_key=doc, node_key=f"{doc}.new", data={}, embedding=vec.tolist()))


def _svc(db, bundles, encoder=None):
    return SubscriptionService(
        db, _RecordingMatcher(bundles), encoder=encoder or _AxisEncoder(),
        default_threshold=0.35, default_top_k=10,
    )


@pytest.mark.asyncio
async def test_full_bundle_floor_is_lowest_match(db):
    svc = _svc(db, {"axis0": [_m("d1", 0.9), _m("d2", 0.8)]})
    sub = await svc.create("p1", ["axis0"], top_k=2)
    assert await svc._floors("p1") == [(sub, "axis0", 0.8)]


@pytest.mark.asyncio
async def test_unfilled_or_empty_bundle_floor_is_threshold(db):
    svc = _svc(db, {"axis0": [_m("d1", 0.9)], "axis1": []})
    sub = await svc.create("p1", ["axis0", "axis1"], top_k=2, threshold=0.5)
    assert sorted(await svc._floors("p1")) == [(sub, "axis0", 0.5), (sub, "axis1", 0.5)]


@pytest.mark.asyncio
async def test_null_top_k_and_threshold_use_defaults(db):
    svc = _svc(db, {"axis0": [_m("d1", 0.9)]})
    sub = await svc.create("p1", ["axis0"])  # top_k=None, threshold=None
    assert await svc._floors("p1") == [(sub, "axis0", 0.35)]


@pytest.mark.asyncio
async def test_node_above_floor_admits_only_that_subscription(db):
    svc = _svc(db, {"axis0": [_m("d1", 0.9), _m("d2", 0.8)], "axis1": [_m("d3", 0.9), _m("d4", 0.8)]})
    sub0 = await svc.create("p1", ["axis0"], top_k=2)
    await svc.create("p1", ["axis1"], top_k=2)
    await _node(db, "p1", "new", _blend(0, 2, 0.85))

    assert await svc._ids_admitting("p1", {"new"}) == {sub0}


@pytest.mark.asyncio
async def test_node_below_floor_admits_nothing_and_reconcile_skips(db):
    svc = _svc(db, {"axis0": [_m("d1", 0.9), _m("d2", 0.8)]})
    await svc.create("p1", ["axis0"], top_k=2)
    await _node(db, "p1", "new", _blend(0, 2, 0.5))
    svc.matcher.calls.clear()

    await svc.reconcile_project("p1", {"new"})

    assert svc.matcher.calls == []


@pytest.mark.asyncio
async def test_keys_without_nodes_or_subscriptions_admit_nothing(db):
    svc = _svc(db, {})
    assert await svc._ids_admitting("p1", {"binary.pdf"}) == set()
    await _node(db, "p1", "new", _unit(0))
    assert await svc._ids_admitting("p1", {"new"}) == set()


@pytest.mark.asyncio
async def test_need_vectors_are_cached(db):
    enc = _AxisEncoder()
    svc = _svc(db, {"axis0": [_m("d1", 0.9)]}, encoder=enc)
    await svc.create("p1", ["axis0"])
    await _node(db, "p1", "new", _unit(0))
    await svc._ids_admitting("p1", {"new"})
    await svc._ids_admitting("p1", {"new"})
    assert enc.calls == 1


@pytest.mark.asyncio
async def test_encoder_failure_reconciles_everything(db):
    class _Broken:
        def encode(self, texts):
            raise RuntimeError("model gone")
    svc = _svc(db, {"axis0": [_m("d1", 0.9)], "axis1": [_m("d3", 0.9)]}, encoder=_Broken())
    await svc.create("p1", ["axis0"])
    await svc.create("p1", ["axis1"])
    await _node(db, "p1", "new", _unit(5))
    svc.matcher.calls.clear()

    await svc.reconcile_project("p1", {"new"})

    assert sorted(svc.matcher.calls) == [("axis0",), ("axis1",)]


@pytest.mark.asyncio
async def test_need_vectors_encode_off_the_event_loop_thread(db):
    class _Recorder(_AxisEncoder):
        def encode(self, texts):
            self.thread = threading.current_thread()
            return super().encode(texts)
    enc = _Recorder()
    svc = _svc(db, {"axis0": [_m("d1", 0.9)]}, encoder=enc)
    await svc.create("p1", ["axis0"])
    await _node(db, "p1", "new", _unit(0))

    await svc._ids_admitting("p1", {"new"})
    assert enc.thread is not threading.main_thread()
