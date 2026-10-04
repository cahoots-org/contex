"""Opt-in relevance reranking with TypeSafe's Jev model.

Search ranks by topical similarity; Jev judges whether each candidate actually
helps with the task. Enabled only when SYSTEM_ONE_ENABLED=true and
TYPESAFE_API_KEY is set. The query and candidate content are sent to
TypeSafe's hosted API.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

import httpx

from src.core.limits import positive_int_env
from src.core.logging import get_logger

logger = get_logger(__name__)

_URL = "https://api.typesafe.ai/v1/systemone"

_RELEVANT = {
    "type": "noul",
    "instructions": (
        "Does `candidate` help carry out `task`? Judge whether it is useful for "
        "the task itself, not whether it shares the task's topic or terms."
    ),
    "criteria": {
        "true": (
            "The candidate supplies something the task needs: an answer, a decision "
            "or its rationale, an explanation, or the code or record the task is about."
        ),
        "false": (
            "The candidate is only on a related topic or uses the same terms, "
            "without supplying anything the task needs."
        ),
    },
}


class JevReranker:
    """Reorders search candidates by Jev's probability that each helps the task."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "jev-latest",
        depth: int = 30,
        concurrency: int = 8,
        max_chars: int = 4000,
        timeout: float = 10.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        """
        Args:
            depth: Top candidates judged; the rest keep search order after them.
            concurrency: Parallel TypeSafe calls per rerank.
            max_chars: Candidate content sent per call; a node's opening is
                enough to judge it, and this bounds tokens.
        """
        self._model = model
        self._depth = depth
        self._concurrency = concurrency
        self._max_chars = max_chars
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> Optional["JevReranker"]:
        if os.getenv("SYSTEM_ONE_ENABLED", "false").lower() != "true":
            return None
        api_key = os.getenv("TYPESAFE_API_KEY")
        if not api_key:
            logger.warning("SYSTEM_ONE_ENABLED is set without TYPESAFE_API_KEY; reranking off")
            return None
        logger.info("Jev relevance reranking enabled")
        return cls(
            api_key,
            model=os.getenv("TYPESAFE_MODEL", "jev-latest"),
            depth=positive_int_env("RERANK_DEPTH", 30),
            concurrency=positive_int_env("RERANK_CONCURRENCY", 8),
            max_chars=positive_int_env("RERANK_MAX_CHARS", 4000),
            timeout=positive_int_env("RERANK_TIMEOUT", 10),
        )

    async def rerank(self, task: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The top ``depth`` candidates sorted by ``relevance``, then the rest.

        Any failure returns the candidates unchanged. Ties keep search order
        (the sort is stable).
        """
        head, tail = candidates[: self._depth], candidates[self._depth :]
        semaphore = asyncio.Semaphore(self._concurrency)

        async def judge(candidate: Dict[str, Any]) -> float:
            async with semaphore:
                return await self._judge(task, candidate)

        try:
            scores = await asyncio.gather(*(judge(c) for c in head))
        except Exception as e:
            logger.warning("Jev rerank failed; keeping search order", error=str(e))
            return candidates
        judged = [{**c, "relevance": score} for c, score in zip(head, scores)]
        return sorted(judged, key=lambda c: c["relevance"], reverse=True) + tail

    async def _judge(self, task: str, candidate: Dict[str, Any]) -> float:
        data = candidate["data"]
        content = data if isinstance(data, str) else json.dumps(data, default=str)
        response = await self._http.post(_URL, json={
            "model": self._model,
            "state": {
                "task": task,
                "candidate": {
                    "source": candidate["document"],
                    "content": content[: self._max_chars],
                },
            },
            "questions": {"relevant": _RELEVANT},
        })
        response.raise_for_status()
        return float(response.json()["answers"]["relevant"]["noul"])
