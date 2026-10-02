"""Read-time symbol join attaches cross-file neighbors to subscription bundles.

The cross-file "edge" is resolved at bundle-build time: a matched node's refs
(call targets / imports) are joined to the nodes that define those names
elsewhere. This runs on create and on every reconcile, so it is immune to ingest
ordering — a ref whose def arrives later links as soon as the def lands.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copy
from unittest.mock import Mock, patch

import numpy as np
import pytest
import pytest_asyncio

from src.core.semantic_matcher import SemanticDataMatcher
from src.core.subscriptions import SubscriptionService

A_PY = "def helper(x):\n    return x + 1\n"           # defines helper
B_PY = "def caller(y):\n    return helper(y)\n"        # refs helper


class _StubMatcher:
    """Returns a fixed bundle so the test controls which nodes are 'matched'."""
    def __init__(self, bundle):
        self._bundle = bundle

    async def match(self, *args, **kwargs):
        return copy.deepcopy(self._bundle)


@pytest_asyncio.fixture
async def ingest(db):
    with patch("src.core.semantic_matcher.SentenceTransformer") as mock_cls:
        model = Mock()
        model.get_sentence_embedding_dimension.return_value = 384
        model.encode.side_effect = lambda x, *a, **k: (
            np.array([0.1] * 384, dtype=np.float32)
            if isinstance(x, str)
            else np.array([[0.1] * 384] * len(x), dtype=np.float32)
        )
        mock_cls.return_value = model
        yield SemanticDataMatcher(db=db)


def _bundle_for(node_key):
    return {"need": [{
        "data_key": node_key, "similarity": 1.0,
        "data": {"value": "..."}, "description": "d",
    }]}


@pytest.mark.asyncio
async def test_matched_node_links_to_cross_file_def(ingest, db, redis):
    await ingest.register_data("p", "repo:a.py", A_PY, "code")
    await ingest.register_data("p", "repo:b.py", B_PY, "code")

    svc = SubscriptionService(db, _StubMatcher({}), redis)
    linked = await svc._link_bundle("p", _bundle_for("repo:b.py.caller"))

    links = linked["need"][0]["links"]
    assert [l["data_key"] for l in links] == ["repo:a.py.helper"]
    assert links[0]["name"] == "helper"


@pytest.mark.asyncio
async def test_link_is_ordering_free(ingest, db, redis):
    # Ref file ingested first: the def doesn't exist yet, so nothing resolves.
    await ingest.register_data("p", "repo:b.py", B_PY, "code")
    svc = SubscriptionService(db, _StubMatcher({}), redis)
    before = await svc._link_bundle("p", _bundle_for("repo:b.py.caller"))
    assert "links" not in before["need"][0]

    # Def arrives later -> re-running the join now resolves it.
    await ingest.register_data("p", "repo:a.py", A_PY, "code")
    after = await svc._link_bundle("p", _bundle_for("repo:b.py.caller"))
    assert after["need"][0]["links"][0]["data_key"] == "repo:a.py.helper"


@pytest.mark.asyncio
async def test_create_persists_linked_bundle(ingest, db, redis):
    await ingest.register_data("p", "repo:a.py", A_PY, "code")
    await ingest.register_data("p", "repo:b.py", B_PY, "code")

    svc = SubscriptionService(db, _StubMatcher(_bundle_for("repo:b.py.caller")), redis)
    sub_id = await svc.create("p", ["need"])
    bundle = await svc.get_bundle(sub_id)
    assert bundle["need"][0]["links"][0]["data_key"] == "repo:a.py.helper"


@pytest.mark.asyncio
async def test_links_resolve_when_def_is_also_matched(ingest, db, redis):
    # Regression: a referrer must still link to a def even when that def node is
    # itself in the matched bundle (both get_user caller and helper are matched).
    await ingest.register_data("p", "repo:a.py", A_PY, "code")   # def helper
    await ingest.register_data("p", "repo:b.py", B_PY, "code")   # caller -> helper

    svc = SubscriptionService(db, _StubMatcher({}), redis)
    bundle = {"need": [
        {"data_key": "repo:b.py.caller", "similarity": 1.0, "data": {}, "description": "d"},
        {"data_key": "repo:a.py.helper", "similarity": 0.9, "data": {}, "description": "d"},
    ]}
    linked = await svc._link_bundle("p", bundle)
    caller = next(m for m in linked["need"] if m["data_key"] == "repo:b.py.caller")
    assert caller["links"][0]["data_key"] == "repo:a.py.helper"


@pytest.mark.asyncio
async def test_no_refs_leaves_bundle_untouched(ingest, db, redis):
    await ingest.register_data("p", "repo:a.py", A_PY, "code")
    svc = SubscriptionService(db, _StubMatcher({}), redis)
    # a.py.helper has no refs -> no links key added.
    linked = await svc._link_bundle("p", _bundle_for("repo:a.py.helper"))
    assert "links" not in linked["need"][0]
