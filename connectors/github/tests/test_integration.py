"""Integration tests for GitHub readers against a mocked GitHub API.

Uses ``pytest-httpx`` to intercept httpx requests and return fixture responses.
If ``pytest_httpx`` is not installed the whole module is skipped gracefully.
"""
from __future__ import annotations

import base64
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

from connectors.github.client import GitHubClient
from connectors.github.readers import read_files, read_issues, read_pulls

_API = "https://api.github.com"
_OWNER = "cahoots-org"
_REPO = "contex"
_SLUG = f"{_OWNER}/{_REPO}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _json_headers() -> dict:
    return {"content-type": "application/json", "X-RateLimit-Remaining": "60"}


def _no_next_headers() -> dict:
    return {**_json_headers(), "link": ""}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_files_yields_correct_keys(httpx_mock: "HTTPXMock") -> None:
    httpx_mock.add_response(
        url=f"{_API}/repos/{_SLUG}",
        json={"default_branch": "main"},
        headers=_json_headers(),
    )
    # The client appends ?recursive=1 as a query param
    httpx_mock.add_response(
        url=re.compile(rf"{re.escape(_API)}/repos/{re.escape(_SLUG)}/git/trees/main"),
        json={
            "tree": [
                {"type": "blob", "path": "src/main.py", "sha": "abc123"},
                # yarn.lock matches *.lock exclude glob
                {"type": "blob", "path": "yarn.lock", "sha": "lck456"},
                # binary — skipped before any network call
                {"type": "blob", "path": "logo.png", "sha": "img789"},
                {"type": "tree", "path": "src", "sha": "tree001"},
            ]
        },
        headers=_json_headers(),
    )
    httpx_mock.add_response(
        url=f"{_API}/repos/{_SLUG}/git/blobs/abc123",
        json={"encoding": "base64", "content": _b64("print('hello')")},
        headers=_json_headers(),
    )

    async with GitHubClient("tok") as client:
        events = []
        async for ev in read_files(
            client,
            _OWNER,
            _REPO,
            exclude=["*.lock"],
        ):
            events.append(ev)

    assert len(events) == 1
    ev = events[0]
    assert ev.key == f"{_SLUG}:src/main.py"
    assert ev.data_format == "text"
    assert ev.payload == "print('hello')"
    assert ev.op == "upsert"
    assert ev.source_meta["source"] == "github"


@pytest.mark.anyio
async def test_read_files_skips_binary_by_default(httpx_mock: "HTTPXMock") -> None:
    httpx_mock.add_response(
        url=f"{_API}/repos/{_SLUG}",
        json={"default_branch": "main"},
        headers=_json_headers(),
    )
    httpx_mock.add_response(
        url=re.compile(rf"{re.escape(_API)}/repos/{re.escape(_SLUG)}/git/trees/main"),
        json={
            "tree": [
                {"type": "blob", "path": "logo.png", "sha": "img789"},
                {"type": "blob", "path": "font.woff2", "sha": "fnt001"},
            ]
        },
        headers=_json_headers(),
    )

    async with GitHubClient("tok") as client:
        events = []
        async for ev in read_files(client, _OWNER, _REPO):
            events.append(ev)

    assert events == []


@pytest.mark.anyio
async def test_read_files_rerun_produces_same_key(httpx_mock: "HTTPXMock") -> None:
    """A second run yields the same stable key — suitable for upsert."""
    for _ in range(2):
        httpx_mock.add_response(
            url=f"{_API}/repos/{_SLUG}",
            json={"default_branch": "main"},
            headers=_json_headers(),
        )
        httpx_mock.add_response(
            url=re.compile(rf"{re.escape(_API)}/repos/{re.escape(_SLUG)}/git/trees/main"),
            json={"tree": [{"type": "blob", "path": "README.md", "sha": "r001"}]},
            headers=_json_headers(),
        )
        httpx_mock.add_response(
            url=f"{_API}/repos/{_SLUG}/git/blobs/r001",
            json={"encoding": "base64", "content": _b64("# readme")},
            headers=_json_headers(),
        )

    keys_run1: list[str] = []
    keys_run2: list[str] = []

    async with GitHubClient("tok") as client:
        async for ev in read_files(client, _OWNER, _REPO):
            keys_run1.append(ev.key)

    async with GitHubClient("tok") as client:
        async for ev in read_files(client, _OWNER, _REPO):
            keys_run2.append(ev.key)

    assert keys_run1 == keys_run2
    assert keys_run1 == [f"{_SLUG}:README.md"]


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_issues_yields_correct_keys(httpx_mock: "HTTPXMock") -> None:
    httpx_mock.add_response(
        url=re.compile(rf"{re.escape(_API)}/repos/{re.escape(_SLUG)}/issues"),
        json=[
            {
                "number": 1,
                "title": "Bug: crash on startup",
                "body": "It crashes.",
                "state": "open",
                "labels": [{"name": "bug"}],
                "user": {"login": "alice"},
            }
        ],
        headers=_no_next_headers(),
    )
    httpx_mock.add_response(
        url=re.compile(rf"{re.escape(_API)}/repos/{re.escape(_SLUG)}/issues/1/comments"),
        json=[{"user": {"login": "bob"}, "body": "Can reproduce."}],
        headers=_no_next_headers(),
    )

    async with GitHubClient("tok") as client:
        events = []
        async for ev in read_issues(client, _OWNER, _REPO, state="open"):
            events.append(ev)

    assert len(events) == 1
    ev = events[0]
    assert ev.key == f"{_SLUG}#1"
    assert ev.op == "upsert"
    assert ev.data_format == "json"
    assert ev.payload["title"] == "Bug: crash on startup"
    assert ev.payload["state"] == "open"
    assert ev.payload["labels"] == ["bug"]
    assert ev.payload["author"] == "alice"
    assert len(ev.payload["comments"]) == 1
    assert ev.payload["comments"][0]["author"] == "bob"


@pytest.mark.anyio
async def test_read_issues_skips_prs(httpx_mock: "HTTPXMock") -> None:
    httpx_mock.add_response(
        url=re.compile(rf"{re.escape(_API)}/repos/{re.escape(_SLUG)}/issues"),
        json=[
            {
                "number": 2,
                "title": "Add feature",
                "body": "...",
                "state": "open",
                "labels": [],
                "user": {"login": "alice"},
                "pull_request": {"url": "..."},
            }
        ],
        headers=_no_next_headers(),
    )

    async with GitHubClient("tok") as client:
        events = []
        async for ev in read_issues(client, _OWNER, _REPO):
            events.append(ev)

    assert events == []


# ---------------------------------------------------------------------------
# Pull requests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_pulls_yields_correct_keys(httpx_mock: "HTTPXMock") -> None:
    httpx_mock.add_response(
        url=re.compile(rf"{re.escape(_API)}/repos/{re.escape(_SLUG)}/pulls"),
        json=[
            {
                "number": 5,
                "title": "feat: add GitHub connector",
                "body": "Implements the connector.",
                "state": "open",
                "labels": [{"name": "enhancement"}],
                "user": {"login": "carol"},
            }
        ],
        headers=_no_next_headers(),
    )
    httpx_mock.add_response(
        url=re.compile(rf"{re.escape(_API)}/repos/{re.escape(_SLUG)}/issues/5/comments"),
        json=[],
        headers=_no_next_headers(),
    )

    async with GitHubClient("tok") as client:
        events = []
        async for ev in read_pulls(client, _OWNER, _REPO):
            events.append(ev)

    assert len(events) == 1
    ev = events[0]
    assert ev.key == f"{_SLUG}!5"
    assert ev.op == "upsert"
    assert ev.data_format == "json"
    assert ev.payload["title"] == "feat: add GitHub connector"
    assert ev.payload["author"] == "carol"
    assert ev.payload["comments"] == []
