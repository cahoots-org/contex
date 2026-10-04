"""JevReranker: opt-in gating, ordering by noul, and fallback on failure."""
import json

import httpx
import pytest

from src.core.relevance import JevReranker


def _candidate(key, doc):
    return {"data_key": key, "document": doc, "similarity": 0.8, "data": {"text": key}, "description": None}


CANDIDATES = [_candidate("a", "doc-a"), _candidate("b", "doc-b"), _candidate("c", "doc-c")]


def _reranker(handler):
    return JevReranker("key", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("env, enabled", [
    ({}, False),
    ({"SYSTEM_ONE_ENABLED": "true"}, False),
    ({"TYPESAFE_API_KEY": "k"}, False),
    ({"SYSTEM_ONE_ENABLED": "true", "TYPESAFE_API_KEY": "k"}, True),
])
def test_from_env_is_opt_in(monkeypatch, env, enabled):
    monkeypatch.delenv("SYSTEM_ONE_ENABLED", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert (JevReranker.from_env() is not None) == enabled


@pytest.mark.asyncio
async def test_rerank_orders_by_noul():
    nouls = {"a": 0.1, "b": 0.9, "c": 0.5}
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append((request, body))
        key = body["state"]["candidate"]["content"]
        noul = nouls[json.loads(key)["text"]]
        return httpx.Response(200, json={"answers": {"relevant": {"type": "noul", "noul": noul}}})

    ranked = await _reranker(handler).rerank("why follow meta-refresh?", CANDIDATES)

    assert [c["data_key"] for c in ranked] == ["b", "c", "a"]
    assert [c["relevance"] for c in ranked] == [0.9, 0.5, 0.1]
    request, body = requests[0]
    assert request.headers["Authorization"] == "Bearer key"
    assert body["model"] == "jev-latest"
    assert body["state"]["task"] == "why follow meta-refresh?"
    assert body["questions"]["relevant"]["type"] == "noul"


@pytest.mark.asyncio
async def test_rerank_failure_keeps_search_order():
    ranked = await _reranker(lambda request: httpx.Response(529)).rerank("task", CANDIDATES)

    assert ranked == CANDIDATES


@pytest.mark.asyncio
async def test_rerank_judges_only_top_depth():
    judged = []

    def handler(request):
        content = json.loads(request.content)["state"]["candidate"]["content"]
        judged.append(json.loads(content)["text"])
        return httpx.Response(200, json={"answers": {"relevant": {"type": "noul", "noul": len(judged) / 10}}})

    reranker = JevReranker("key", depth=2, concurrency=1, transport=httpx.MockTransport(handler))
    ranked = await reranker.rerank("task", CANDIDATES)

    assert judged == ["a", "b"]
    assert [c["data_key"] for c in ranked] == ["b", "a", "c"]
    assert "relevance" not in ranked[2]
