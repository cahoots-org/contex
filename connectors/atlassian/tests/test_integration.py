"""Integration tests for the readers against a mocked Atlassian API.

Uses ``pytest-httpx`` to intercept httpx requests. If it is not installed the
whole module is skipped gracefully.
"""
from __future__ import annotations

import re

import pytest

try:
    import pytest_httpx  # noqa: F401
    _HAVE_PYTEST_HTTPX = True
except ImportError:
    _HAVE_PYTEST_HTTPX = False

pytestmark = pytest.mark.skipif(
    not _HAVE_PYTEST_HTTPX,
    reason="pytest-httpx not installed; skipping integration tests",
)

if _HAVE_PYTEST_HTTPX:
    from pytest_httpx import HTTPXMock

from connectors.atlassian.client import AtlassianClient
from connectors.atlassian.readers import read_confluence_pages, read_jira_issues

SITE = "https://acme.atlassian.net"
EMAIL = "you@acme.com"
TOKEN = "tok"


def _headers() -> dict:
    return {"content-type": "application/json"}


def _url(path: str) -> "re.Pattern":
    return re.compile(re.escape(SITE) + re.escape(path))


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_jira_issues_inline_comments(httpx_mock: "HTTPXMock") -> None:
    httpx_mock.add_response(
        method="POST",
        url=_url("/rest/api/3/search/jql"),
        json={
            "isLast": True,
            "issues": [
                {
                    "key": "DEV-42",
                    "fields": {
                        "summary": "Fix login",
                        "description": {
                            "type": "doc",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "broken"}]}
                            ],
                        },
                        "issuetype": {"name": "Bug"},
                        "status": {"name": "Done"},
                        "labels": ["auth"],
                        "updated": "2026-02-01T00:00:00.000+0000",
                        "comment": {
                            "total": 1,
                            "comments": [
                                {
                                    "author": {"displayName": "Bob"},
                                    "body": {
                                        "type": "doc",
                                        "content": [
                                            {"type": "paragraph", "content": [{"type": "text", "text": "nice"}]}
                                        ],
                                    },
                                    "created": "2026-02-02T00:00:00.000+0000",
                                }
                            ],
                        },
                    },
                }
            ],
        },
        headers=_headers(),
    )

    async with AtlassianClient(SITE, EMAIL, TOKEN) as client:
        events = [
            ev
            async for ev in read_jira_issues(client, site_url=SITE, projects=["DEV"])
        ]

    assert len(events) == 1
    ev = events[0]
    assert ev.key == "jira:DEV-42"
    assert ev.payload["summary"] == "Fix login"
    assert ev.payload["description"] == "broken"
    assert ev.payload["labels"] == ["auth"]
    assert ev.payload["comments"] == [
        {"author": "Bob", "body": "nice", "created": "2026-02-02T00:00:00.000+0000"}
    ]


@pytest.mark.anyio
async def test_read_jira_issues_pages_large_comment_thread(httpx_mock: "HTTPXMock") -> None:
    # comment.total (2) > inline comments (0) -> the reader pages the thread.
    httpx_mock.add_response(
        method="POST",
        url=_url("/rest/api/3/search/jql"),
        json={
            "isLast": True,
            "issues": [
                {
                    "key": "DEV-7",
                    "fields": {
                        "summary": "big thread",
                        "updated": "2026-01-01T00:00:00.000+0000",
                        "comment": {"total": 2, "comments": []},
                    },
                }
            ],
        },
        headers=_headers(),
    )
    httpx_mock.add_response(
        method="GET",
        url=_url("/rest/api/3/issue/DEV-7/comment"),
        json={
            "total": 2,
            "comments": [
                {"author": {"displayName": "A"}, "body": None, "created": "t1"},
                {"author": {"displayName": "B"}, "body": None, "created": "t2"},
            ],
        },
        headers=_headers(),
    )

    async with AtlassianClient(SITE, EMAIL, TOKEN) as client:
        events = [ev async for ev in read_jira_issues(client, site_url=SITE)]

    assert len(events) == 1
    authors = [c["author"] for c in events[0].payload["comments"]]
    assert authors == ["A", "B"]


# ---------------------------------------------------------------------------
# Confluence
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_confluence_pages(httpx_mock: "HTTPXMock") -> None:
    httpx_mock.add_response(
        method="GET",
        url=_url("/wiki/api/v2/spaces"),
        json={
            "results": [
                {"id": "131275", "key": "ENG", "type": "global"},
                {"id": "999", "key": "~rob", "type": "personal"},  # skipped
            ],
            "_links": {},
        },
        headers=_headers(),
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(re.escape(SITE) + r"/wiki/api/v2/pages\?"),
        json={
            "results": [
                {
                    "id": "33046",
                    "title": "Runbook",
                    "spaceId": "131275",
                    "body": {"storage": {"value": "<p>do the thing</p>"}},
                    "version": {"number": 3, "createdAt": "2026-03-01T00:00:00Z", "authorId": "acc1"},
                    "_links": {"webui": "/spaces/ENG/pages/33046/Runbook"},
                }
            ],
            "_links": {},
        },
        headers=_headers(),
    )
    httpx_mock.add_response(
        method="GET",
        url=_url("/wiki/api/v2/pages/33046/labels"),
        json={"results": [{"name": "runbook"}], "_links": {}},
        headers=_headers(),
    )
    httpx_mock.add_response(
        method="GET",
        url=_url("/wiki/api/v2/pages/33046/footer-comments"),
        json={
            "results": [
                {"version": {"authorId": "acc2"}, "body": {"storage": {"value": "<p>nice page</p>"}}}
            ],
            "_links": {},
        },
        headers=_headers(),
    )

    async with AtlassianClient(SITE, EMAIL, TOKEN) as client:
        events = [
            ev
            async for ev in read_confluence_pages(client, site_url=SITE, spaces=["ENG"])
        ]

    assert len(events) == 1  # personal space skipped
    ev = events[0]
    assert ev.key == "confluence:33046"
    assert ev.payload["title"] == "Runbook"
    assert ev.payload["space"] == "ENG"
    assert ev.payload["body"] == "do the thing"
    assert ev.payload["labels"] == ["runbook"]
    assert ev.payload["comments"] == [{"author": "acc2", "body": "nice page"}]
    assert ev.payload["url"] == f"{SITE}/wiki/spaces/ENG/pages/33046/Runbook"


@pytest.mark.anyio
async def test_read_confluence_skips_comments_when_disabled(httpx_mock: "HTTPXMock") -> None:
    httpx_mock.add_response(
        method="GET",
        url=_url("/wiki/api/v2/spaces"),
        json={"results": [{"id": "1", "key": "ENG", "type": "global"}], "_links": {}},
        headers=_headers(),
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(re.escape(SITE) + r"/wiki/api/v2/pages\?"),
        json={
            "results": [
                {
                    "id": "5",
                    "title": "P",
                    "spaceId": "1",
                    "body": {"storage": {"value": "<p>x</p>"}},
                    "version": {"number": 1, "createdAt": "2026-01-01T00:00:00Z"},
                    "_links": {"webui": "/x"},
                }
            ],
            "_links": {},
        },
        headers=_headers(),
    )
    httpx_mock.add_response(
        method="GET",
        url=_url("/wiki/api/v2/pages/5/labels"),
        json={"results": [], "_links": {}},
        headers=_headers(),
    )

    async with AtlassianClient(SITE, EMAIL, TOKEN) as client:
        events = [
            ev
            async for ev in read_confluence_pages(
                client, site_url=SITE, include_comments=False
            )
        ]

    assert len(events) == 1
    assert events[0].payload["comments"] == []
