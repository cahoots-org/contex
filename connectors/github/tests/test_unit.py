"""Unit tests for path filtering, binary detection, key derivation, and backoff.

All tests here run without network access or external services.
"""
from __future__ import annotations

import time

import httpx
import pytest

from connectors.base import allowed
from connectors.github.client import GitHubClient, _next_link, _rate_limit_wait
from connectors.github.readers import commit_to_event, is_binary_path


# ---------------------------------------------------------------------------
# Binary extension detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, expected",
    [
        ("src/main.py", False),
        ("README.md", False),
        ("data/image.PNG", True),
        ("build/app.exe", True),
        ("vendor/lib.so", True),
        ("fonts/icon.woff2", True),
        ("archive.tar.gz", True),
        ("schema.sql", False),
        ("Makefile", False),
        ("dist/bundle.js", False),
        ("dist/bundle.min.js", False),
        ("image.JPEG", True),
    ],
)
def test_is_binary_path(path: str, expected: bool) -> None:
    assert is_binary_path(path) == expected


# ---------------------------------------------------------------------------
# Path allow/deny (using the shared base `allowed`)
# ---------------------------------------------------------------------------


def test_allowed_no_filters():
    assert allowed("src/foo.py") is True


def test_allowed_include_match():
    assert allowed("src/foo.py", include=["src/*"]) is True


def test_allowed_include_miss():
    assert allowed("docs/README.md", include=["src/*"]) is False


def test_allowed_exclude_match():
    # yarn.lock matches *.lock; package-lock.json does not (fnmatch semantics)
    assert allowed("yarn.lock", exclude=["*.lock"]) is False


def test_allowed_exclude_vendor():
    assert allowed("vendor/github.com/pkg/lib.go", exclude=["vendor/*"]) is False


def test_allowed_include_then_exclude():
    assert allowed("src/lib.min.js", include=["src/*"], exclude=["*.min.js"]) is False


def test_allowed_include_and_no_exclude():
    assert allowed("src/lib.py", include=["src/*"], exclude=["*.lock"]) is True


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def test_file_key():
    key = f"cahoots-org/contex:src/main.py"
    assert key == "cahoots-org/contex:src/main.py"


def test_issue_key():
    owner, repo, number = "cahoots-org", "contex", 42
    key = f"{owner}/{repo}#{number}"
    assert key == "cahoots-org/contex#42"


def test_pull_key():
    owner, repo, number = "cahoots-org", "contex", 7
    key = f"{owner}/{repo}!{number}"
    assert key == "cahoots-org/contex!7"


# ---------------------------------------------------------------------------
# Commit mapping
# ---------------------------------------------------------------------------


def test_commit_to_event_full_metadata():
    detail = {
        "sha": "abc123",
        "html_url": "https://github.com/cahoots-org/contex/commit/abc123",
        "commit": {
            "message": "Add currency arg to charge()",
            "author": {"name": "Ada", "email": "ada@example.com", "date": "2026-02-01T10:00:00Z"},
            "committer": {"name": "Ada", "email": "ada@example.com", "date": "2026-02-01T10:05:00Z"},
        },
        "author": {"login": "ada"},
        "committer": {"login": "ada"},
        "parents": [{"sha": "def456"}],
        "stats": {"additions": 12, "deletions": 3, "total": 15},
        "files": [
            {"filename": "payments/charge.py", "status": "modified", "additions": 12, "deletions": 3},
        ],
    }
    ev = commit_to_event("cahoots-org", "contex", detail)

    assert ev.key == "cahoots-org/contex@abc123"
    assert ev.op == "upsert"
    assert ev.data_format == "json"
    assert ev.payload["message"] == "Add currency arg to charge()"
    assert ev.payload["author"]["login"] == "ada"
    assert ev.payload["author"]["email"] == "ada@example.com"
    assert ev.payload["parents"] == ["def456"]
    assert ev.payload["stats"] == {"additions": 12, "deletions": 3, "total": 15}
    assert ev.payload["files"] == [
        {"filename": "payments/charge.py", "status": "modified", "additions": 12, "deletions": 3}
    ]
    assert ev.source_meta == {"source": "github", "owner": "cahoots-org", "repo": "contex", "sha": "abc123"}


def test_commit_to_event_missing_fields_default_safely():
    ev = commit_to_event("o", "r", {"sha": "s1", "commit": {}})
    assert ev.key == "o/r@s1"
    assert ev.payload["message"] == ""
    assert ev.payload["files"] == []
    assert ev.payload["parents"] == []
    assert ev.payload["stats"] == {"additions": 0, "deletions": 0, "total": 0}


# ---------------------------------------------------------------------------
# Rate-limit backoff helpers
# ---------------------------------------------------------------------------


def _make_response(status: int, headers: dict) -> httpx.Response:
    """Build a minimal httpx.Response for header inspection."""
    return httpx.Response(status_code=status, headers=headers, content=b"")


def test_rate_limit_wait_no_exhaustion():
    resp = _make_response(200, {"X-RateLimit-Remaining": "50"})
    assert _rate_limit_wait(resp) == 0.0


def test_rate_limit_wait_zero_remaining_with_reset():
    reset_epoch = str(int(time.time()) + 30)
    resp = _make_response(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset_epoch})
    wait = _rate_limit_wait(resp)
    assert 28.0 <= wait <= 33.0


def test_rate_limit_wait_zero_remaining_no_reset():
    resp = _make_response(429, {"X-RateLimit-Remaining": "0"})
    assert _rate_limit_wait(resp) == 60.0


def test_rate_limit_wait_missing_remaining():
    resp = _make_response(200, {})
    assert _rate_limit_wait(resp) == 0.0


def test_rate_limit_wait_honors_retry_after():
    resp = _make_response(429, {"Retry-After": "12", "X-RateLimit-Remaining": "42"})
    assert _rate_limit_wait(resp) == 12.0


def test_rate_limit_wait_retry_after_non_numeric():
    resp = _make_response(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert _rate_limit_wait(resp) == 60.0


def test_no_token_logs_warning(caplog):
    with caplog.at_level("WARNING"):
        GitHubClient("")
    assert any("unauthenticated" in r.message for r in caplog.records)


def test_token_no_warning(caplog):
    with caplog.at_level("WARNING"):
        GitHubClient("tok")
    assert not any("unauthenticated" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Link header parsing
# ---------------------------------------------------------------------------


def test_next_link_present():
    header = '<https://api.github.com/repos/x/y/issues?page=2>; rel="next", <https://api.github.com/repos/x/y/issues?page=5>; rel="last"'
    assert _next_link(header) == "https://api.github.com/repos/x/y/issues?page=2"


def test_next_link_absent():
    header = '<https://api.github.com/repos/x/y/issues?page=5>; rel="last"'
    assert _next_link(header) is None


def test_next_link_empty():
    assert _next_link("") is None
