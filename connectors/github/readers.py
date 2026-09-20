"""GitHub resource readers: files, issues, and pull requests.

Each reader is an async generator that yields :class:`~connectors.base.ChangeEvent`
objects. All I/O goes through :class:`~connectors.github.client.GitHubClient`.
"""
from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator
from typing import Any

from connectors.base import ChangeEvent, allowed

from .client import GitHubClient

log = logging.getLogger(__name__)

_BINARY_EXTENSIONS = frozenset(
    {
        # compiled / object
        ".pyc", ".pyo", ".o", ".a", ".so", ".dylib", ".dll", ".exe", ".lib",
        # archives
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
        ".whl", ".egg",
        # images
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
        ".tiff", ".tif",
        # audio / video
        ".mp3", ".mp4", ".wav", ".ogg", ".flac", ".avi", ".mov", ".mkv",
        ".webm",
        # fonts
        ".ttf", ".otf", ".woff", ".woff2", ".eot",
        # documents / data
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".sqlite", ".db",
        # other binary
        ".bin", ".dat", ".class",
    }
)


def is_binary_path(path: str) -> bool:
    """True when ``path`` has an extension known to be binary."""
    dot = path.rfind(".")
    if dot == -1:
        return False
    return path[dot:].lower() in _BINARY_EXTENSIONS


async def read_files(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    include_binary: bool = False,
) -> AsyncIterator[ChangeEvent]:
    """Yield one ChangeEvent per text file on the default branch."""
    repo_data = await client.get(f"/repos/{owner}/{repo}")
    default_branch = repo_data.get("default_branch", "main")

    tree_data = await client.get(
        f"/repos/{owner}/{repo}/git/trees/{default_branch}",
        recursive=1,
    )
    blobs = [item for item in tree_data.get("tree", []) if item.get("type") == "blob"]

    for blob in blobs:
        path: str = blob.get("path", "")
        if not include_binary and is_binary_path(path):
            log.debug("skip binary %s/%s:%s", owner, repo, path)
            continue
        if not allowed(path, include=include, exclude=exclude):
            log.debug("skip excluded %s/%s:%s", owner, repo, path)
            continue

        sha = blob.get("sha", "")
        try:
            content_data = await client.get(f"/repos/{owner}/{repo}/git/blobs/{sha}")
        except Exception as exc:
            log.warning("failed to fetch %s/%s:%s — %s", owner, repo, path, exc)
            continue

        encoding = content_data.get("encoding", "")
        raw = content_data.get("content", "")
        if encoding == "base64":
            try:
                text = base64.b64decode(raw).decode("utf-8", errors="replace")
            except Exception as exc:
                log.warning("decode error %s/%s:%s — %s", owner, repo, path, exc)
                continue
        else:
            text = raw

        key = f"{owner}/{repo}:{path}"
        log.debug("file %s", key)
        yield ChangeEvent(
            op="upsert",
            key=key,
            payload=text,
            source_meta={"source": "github", "owner": owner, "repo": repo, "path": path},
            data_format="text",
        )


async def _fetch_comments(
    client: GitHubClient, owner: str, repo: str, number: int, kind: str
) -> list[dict]:
    """Fetch comment bodies for an issue or pull request."""
    comments: list[dict] = []
    path = f"/repos/{owner}/{repo}/issues/{number}/comments"
    async for comment in client.paginate(path):
        comments.append({"author": _login(comment), "body": comment.get("body", "")})
    return comments


async def read_issues(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    state: str = "all",
) -> AsyncIterator[ChangeEvent]:
    """Yield one ChangeEvent per issue (excludes pull requests)."""
    async for issue in client.paginate(
        f"/repos/{owner}/{repo}/issues",
        state=state,
    ):
        if issue.get("pull_request"):
            continue  # GitHub issues API returns PRs too; skip them

        number: int = issue["number"]
        comments = await _fetch_comments(client, owner, repo, number, "issue")
        key = f"{owner}/{repo}#{number}"
        log.debug("issue %s", key)
        yield ChangeEvent(
            op="upsert",
            key=key,
            payload={
                "title": issue.get("title", ""),
                "body": issue.get("body", ""),
                "state": issue.get("state", ""),
                "labels": [lbl.get("name", "") for lbl in issue.get("labels", [])],
                "author": _login(issue),
                "comments": comments,
            },
            source_meta={"source": "github", "owner": owner, "repo": repo, "number": number},
        )


async def read_pulls(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    state: str = "all",
) -> AsyncIterator[ChangeEvent]:
    """Yield one ChangeEvent per pull request."""
    async for pull in client.paginate(
        f"/repos/{owner}/{repo}/pulls",
        state=state,
    ):
        number: int = pull["number"]
        comments = await _fetch_comments(client, owner, repo, number, "pull")
        key = f"{owner}/{repo}!{number}"
        log.debug("pull %s", key)
        yield ChangeEvent(
            op="upsert",
            key=key,
            payload={
                "title": pull.get("title", ""),
                "body": pull.get("body", ""),
                "state": pull.get("state", ""),
                "labels": [lbl.get("name", "") for lbl in pull.get("labels", [])],
                "author": _login(pull),
                "comments": comments,
            },
            source_meta={"source": "github", "owner": owner, "repo": repo, "number": number},
        )


def _login(obj: dict[str, Any]) -> str:
    user = obj.get("user") or {}
    return user.get("login", "")
