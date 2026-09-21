"""Jira and Confluence readers.

Each reader is an async generator of :class:`~connectors.base.ChangeEvent`.
Enumeration/pagination is deliberately separated from the *pure* entity->event
mappers (:func:`issue_to_event`, :func:`page_to_event`) and the pure
:func:`build_jql`, so a future webhook receiver can reuse the mappers on a
single payload without touching the crawl logic.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence

from connectors.base import ChangeEvent

from .client import AtlassianClient
from .convert import adf_to_text, storage_html_to_text

log = logging.getLogger(__name__)

# Jira fields requested per issue. ``comment`` is included so small threads need
# no follow-up call; large threads are paged separately (see read_jira_issues).
JIRA_FIELDS = [
    "summary",
    "description",
    "issuetype",
    "status",
    "priority",
    "resolution",
    "labels",
    "components",
    "assignee",
    "reporter",
    "created",
    "updated",
    "parent",
    "comment",
]


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _name(obj: object) -> str:
    return obj.get("name", "") if isinstance(obj, dict) else ""


def _display(obj: object) -> str:
    return obj.get("displayName", "") if isinstance(obj, dict) else ""


def build_jql(
    projects: Sequence[str] | None,
    *,
    extra_jql: str | None = None,
    since: str | None = None,
) -> str:
    """Compose a JQL string from a project allow-list, a since filter, and a
    caller-supplied clause, always ending in a stable ``ORDER BY created ASC``.
    """
    clauses: list[str] = []
    if projects:
        joined = ", ".join(f'"{p}"' for p in projects)
        clauses.append(f"project in ({joined})")
    if since:
        clauses.append(f'updated >= "{since}"')
    if extra_jql:
        clauses.append(f"({extra_jql})")
    if not clauses:
        # Jira's enhanced search rejects unbounded JQL, so bound "everything"
        # with a lower date that predates any possible issue.
        clauses.append('created >= "1970-01-01"')
    where = " AND ".join(clauses)
    return f"{where} ORDER BY created ASC".strip()


def _comment_dict(raw: dict) -> dict:
    return {
        "author": _display(raw.get("author")),
        "body": adf_to_text(raw.get("body")),
        "created": raw.get("created"),
    }


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


def issue_to_event(issue: dict, *, site_url: str, comments: list[dict]) -> ChangeEvent:
    """Map one Jira issue (with resolved comments) to a ChangeEvent. Pure."""
    fields = issue.get("fields") or {}
    key = issue["key"]
    url = f"{site_url.rstrip('/')}/browse/{key}"
    payload = {
        "key": key,
        "summary": fields.get("summary") or "",
        "description": adf_to_text(fields.get("description")),
        "type": _name(fields.get("issuetype")),
        "status": _name(fields.get("status")),
        "priority": _name(fields.get("priority")),
        "resolution": _name(fields.get("resolution")),
        "labels": fields.get("labels") or [],
        "components": [_name(c) for c in fields.get("components") or []],
        "assignee": _display(fields.get("assignee")),
        "reporter": _display(fields.get("reporter")),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "parent": (fields.get("parent") or {}).get("key"),
        "url": url,
        "comments": comments,
    }
    return ChangeEvent(
        op="upsert",
        key=f"jira:{key}",
        payload=payload,
        source_meta={
            "source": "jira",
            "project": key.split("-", 1)[0],
            "key": key,
            "url": url,
            "updated": payload["updated"],
        },
    )


async def _all_issue_comments(client: AtlassianClient, key: str) -> list[dict]:
    """Page the full comment thread of an issue (classic startAt pagination)."""
    out: list[dict] = []
    start = 0
    while True:
        data = await client.get(
            f"/rest/api/3/issue/{key}/comment", startAt=start, maxResults=100
        )
        page = data.get("comments", [])
        out.extend(_comment_dict(c) for c in page)
        start += len(page)
        if not page or start >= int(data.get("total", 0)):
            return out


async def read_jira_issues(
    client: AtlassianClient,
    *,
    site_url: str,
    projects: Sequence[str] | None = None,
    extra_jql: str | None = None,
    since: str | None = None,
    include_comments: bool = True,
) -> AsyncIterator[ChangeEvent]:
    """Yield one ChangeEvent per Jira issue matching the project/since filters."""
    jql = build_jql(projects, extra_jql=extra_jql, since=since)
    log.info("jira search: %s", jql)
    async for issue in client.paginate_jira(jql, fields=JIRA_FIELDS):
        key = issue["key"]
        comment_field = (issue.get("fields") or {}).get("comment") or {}
        if not include_comments:
            comments: list[dict] = []
        elif int(comment_field.get("total", 0)) > len(comment_field.get("comments", [])):
            comments = await _all_issue_comments(client, key)
        else:
            comments = [_comment_dict(c) for c in comment_field.get("comments", [])]
        yield issue_to_event(issue, site_url=site_url, comments=comments)


# ---------------------------------------------------------------------------
# Confluence
# ---------------------------------------------------------------------------


def page_to_event(
    page: dict,
    *,
    site_url: str,
    space_key: str,
    body_text: str,
    labels: list[str],
    comments: list[dict],
) -> ChangeEvent:
    """Map one Confluence page (with resolved body/labels/comments) to an event. Pure."""
    page_id = str(page.get("id", ""))
    version = page.get("version") or {}
    webui = (page.get("_links") or {}).get("webui") or ""
    url = f"{site_url.rstrip('/')}/wiki{webui}" if webui else ""
    payload = {
        "id": page_id,
        "title": page.get("title", ""),
        "space": space_key,
        "body": body_text,
        "version": version.get("number"),
        "author": version.get("authorId") or page.get("authorId"),
        "created": page.get("createdAt"),
        "updated": version.get("createdAt"),
        "labels": labels,
        "url": url,
        "comments": comments,
    }
    return ChangeEvent(
        op="upsert",
        key=f"confluence:{page_id}",
        payload=payload,
        source_meta={
            "source": "confluence",
            "spaceId": str(page.get("spaceId", "")),
            "id": page_id,
            "url": url,
            "updated": payload["updated"],
        },
    )


async def _page_labels(client: AtlassianClient, page_id: str) -> list[str]:
    labels: list[str] = []
    async for label in client.paginate_confluence(
        f"/wiki/api/v2/pages/{page_id}/labels", limit=100
    ):
        name = label.get("name")
        if name:
            labels.append(name)
    return labels


async def _page_comments(client: AtlassianClient, page_id: str) -> list[dict]:
    comments: list[dict] = []
    async for comment in client.paginate_confluence(
        f"/wiki/api/v2/pages/{page_id}/footer-comments",
        limit=100,
        **{"body-format": "storage"},
    ):
        body = ((comment.get("body") or {}).get("storage") or {}).get("value", "")
        comments.append(
            {
                "author": (comment.get("version") or {}).get("authorId", ""),
                "body": storage_html_to_text(body),
            }
        )
    return comments


async def _space_key_by_id(
    client: AtlassianClient,
    *,
    spaces: Sequence[str] | None,
    include_personal: bool,
) -> dict[str, str]:
    """Build ``{space_id: space_key}`` for the spaces we should ingest."""
    wanted = set(spaces) if spaces else None
    mapping: dict[str, str] = {}
    async for space in client.paginate_confluence("/wiki/api/v2/spaces", limit=100):
        if not include_personal and space.get("type") == "personal":
            continue
        key = space.get("key")
        if wanted is not None and key not in wanted:
            continue
        mapping[str(space["id"])] = key or ""
    return mapping


async def read_confluence_pages(
    client: AtlassianClient,
    *,
    site_url: str,
    spaces: Sequence[str] | None = None,
    include_personal: bool = False,
    include_comments: bool = True,
    since: str | None = None,
) -> AsyncIterator[ChangeEvent]:
    """Yield one ChangeEvent per current Confluence page in the selected spaces."""
    space_map = await _space_key_by_id(
        client, spaces=spaces, include_personal=include_personal
    )
    log.info("confluence spaces: %s", ", ".join(space_map.values()) or "(none)")
    for space_id, space_key in space_map.items():
        async for page in client.paginate_confluence(
            "/wiki/api/v2/pages",
            limit=100,
            **{"space-id": space_id, "body-format": "storage"},
        ):
            updated = (page.get("version") or {}).get("createdAt", "")
            if since and updated and updated < since:
                continue
            body_html = ((page.get("body") or {}).get("storage") or {}).get("value", "")
            page_id = str(page["id"])
            labels = await _page_labels(client, page_id)
            comments = await _page_comments(client, page_id) if include_comments else []
            yield page_to_event(
                page,
                site_url=site_url,
                space_key=space_key,
                body_text=storage_html_to_text(body_html),
                labels=labels,
                comments=comments,
            )
