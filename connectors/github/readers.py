"""GitHub resource readers: files, issues, pull requests, and commits.

Each reader is an async generator that yields :class:`~connectors.base.ChangeEvent`
objects. All I/O goes through :class:`~connectors.github.client.GitHubClient`.
"""
from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

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
        comments.append(
            {
                "author": _login(comment),
                "body": comment.get("body", ""),
                "created_at": comment.get("created_at", ""),
            }
        )
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
                "created_at": issue.get("created_at", ""),
                "updated_at": issue.get("updated_at", ""),
                "closed_at": issue.get("closed_at", ""),
                "comments": comments,
            },
            source_meta={
                "source": "github",
                "owner": owner,
                "repo": repo,
                "number": number,
                "updated_at": issue.get("updated_at", ""),
            },
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
                "created_at": pull.get("created_at", ""),
                "updated_at": pull.get("updated_at", ""),
                "closed_at": pull.get("closed_at", ""),
                "merged_at": pull.get("merged_at", ""),
                "comments": comments,
            },
            source_meta={
                "source": "github",
                "owner": owner,
                "repo": repo,
                "number": number,
                "updated_at": pull.get("updated_at", ""),
            },
        )


def commit_to_event(owner: str, repo: str, commit: dict[str, Any]) -> ChangeEvent:
    """Map a single-commit detail payload to a ChangeEvent.

    ``commit`` is the response from ``GET /repos/{owner}/{repo}/commits/{sha}``,
    which carries ``stats`` and the changed ``files`` that the list endpoint omits.
    """
    sha = commit.get("sha", "")
    meta = commit.get("commit") or {}
    author = meta.get("author") or {}
    committer = meta.get("committer") or {}
    stats = commit.get("stats") or {}
    files = [
        {
            "filename": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
        }
        for f in commit.get("files") or []
    ]
    return ChangeEvent(
        op="upsert",
        key=f"{owner}/{repo}@{sha}",
        payload={
            "sha": sha,
            "message": meta.get("message", ""),
            "author": {
                "name": author.get("name", ""),
                "email": author.get("email", ""),
                "login": (commit.get("author") or {}).get("login", ""),
                "date": author.get("date", ""),
            },
            "committer": {
                "name": committer.get("name", ""),
                "email": committer.get("email", ""),
                "login": (commit.get("committer") or {}).get("login", ""),
                "date": committer.get("date", ""),
            },
            "parents": [p.get("sha", "") for p in commit.get("parents") or []],
            "url": commit.get("html_url", ""),
            "stats": {
                "additions": stats.get("additions", 0),
                "deletions": stats.get("deletions", 0),
                "total": stats.get("total", 0),
            },
            "files": files,
        },
        source_meta={"source": "github", "owner": owner, "repo": repo, "sha": sha},
    )


async def _resolve_commit_since(
    client: GitHubClient, owner: str, repo: str, since: str | None
) -> str | None:
    """Resolve the lower bound for commit history.

    An explicit ``since`` wins. Otherwise fall back to the latest published
    release's date. Returns ``None`` when neither is available, which the caller
    treats as "skip commits" rather than crawling all of history.
    """
    if since:
        return since
    try:
        release = await client.get(f"/repos/{owner}/{repo}/releases/latest")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    return release.get("published_at") or release.get("created_at")


async def read_commits(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    since: str | None = None,
    branch: str | None = None,
) -> AsyncIterator[ChangeEvent]:
    """Yield one ChangeEvent per commit, with full per-commit metadata.

    History is bounded by ``since`` (an explicit date, else the latest release).
    Without a bound, commits are skipped rather than crawling all of history.
    Each commit needs a detail fetch, since the list endpoint omits files/stats.
    """
    resolved_since = await _resolve_commit_since(client, owner, repo, since)
    if resolved_since is None:
        log.warning(
            "%s/%s: no commits.since and no published release; skipping commits", owner, repo
        )
        return

    if branch is None:
        repo_data = await client.get(f"/repos/{owner}/{repo}")
        branch = repo_data.get("default_branch", "main")

    async for commit in client.paginate(
        f"/repos/{owner}/{repo}/commits",
        sha=branch,
        since=resolved_since,
    ):
        sha = commit.get("sha")
        if not sha:
            continue
        try:
            detail = await client.get(f"/repos/{owner}/{repo}/commits/{sha}")
        except Exception as exc:
            log.warning("failed to fetch commit %s/%s@%s — %s", owner, repo, sha, exc)
            continue
        log.debug("commit %s/%s@%s", owner, repo, sha)
        yield commit_to_event(owner, repo, detail)


def _login(obj: dict[str, Any]) -> str:
    user = obj.get("user") or {}
    return user.get("login", "")
