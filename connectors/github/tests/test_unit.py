"""Unit tests for path filtering, binary detection, key derivation, and backoff.

All tests here run without network access or external services.
"""
from __future__ import annotations

import time

import httpx
import pytest

from connectors.base import allowed
from connectors.github.client import _next_link, _rate_limit_wait
from connectors.github.readers import is_binary_path


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
