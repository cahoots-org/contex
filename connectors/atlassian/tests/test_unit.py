"""Unit tests for text conversion, JQL building, mappers, and backoff.

All tests here run without network access or external services.
"""
from __future__ import annotations

import httpx
import pytest

from connectors.atlassian.client import _retry_after
from connectors.atlassian.convert import adf_to_text, storage_html_to_text
from connectors.atlassian.readers import (
    build_jql,
    issue_to_event,
    page_to_event,
)

SITE = "https://acme.atlassian.net"


# ---------------------------------------------------------------------------
# ADF -> text
# ---------------------------------------------------------------------------


def test_adf_none_and_string():
    assert adf_to_text(None) == ""
    assert adf_to_text("legacy plain text") == "legacy plain text"


def test_adf_paragraphs_and_lists():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Hello "},
                    {"type": "text", "text": "world"},
                ],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "one"}]}
                        ],
                    },
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "two"}]}
                        ],
                    },
                ],
            },
        ],
    }
    text = adf_to_text(doc)
    assert "Hello world" in text
    assert "- one" in text
    assert "- two" in text
    # no runaway blank lines
    assert "\n\n\n" not in text


def test_adf_mention_hardbreak_and_unknown_node():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "ping "},
                    {"type": "mention", "attrs": {"text": "@rob"}},
                    {"type": "hardBreak"},
                    {"type": "someFutureNode", "content": [{"type": "text", "text": "kept"}]},
                ],
            }
        ],
    }
    text = adf_to_text(doc)
    assert "ping @rob" in text
    assert "kept" in text  # unknown nodes degrade to their children


# ---------------------------------------------------------------------------
# storage HTML -> text
# ---------------------------------------------------------------------------


def test_storage_html_basic():
    html = "<h1>Title</h1><p>Para one.</p><ul><li>a</li><li>b</li></ul>"
    text = storage_html_to_text(html)
    assert "Title" in text
    assert "Para one." in text
    assert "- a" in text
    assert "- b" in text


def test_storage_html_skips_macro_params():
    html = "<ac:structured-macro><ac:parameter>noise</ac:parameter>real body</ac:structured-macro>"
    text = storage_html_to_text(html)
    assert "real body" in text
    assert "noise" not in text


def test_storage_html_empty():
    assert storage_html_to_text("") == ""


# ---------------------------------------------------------------------------
# JQL building
# ---------------------------------------------------------------------------


def test_build_jql_projects():
    assert build_jql(["DEV", "CONT"]) == 'project in ("DEV", "CONT") ORDER BY created ASC'


def test_build_jql_empty_is_bounded():
    # Enhanced search rejects unbounded JQL, so "all issues" gets a date bound.
    assert build_jql(None) == 'created >= "1970-01-01" ORDER BY created ASC'


def test_build_jql_since_and_extra():
    jql = build_jql(["DEV"], extra_jql="labels = kb", since="2026-01-01")
    assert 'project in ("DEV")' in jql
    assert 'updated >= "2026-01-01"' in jql
    assert "(labels = kb)" in jql
    assert jql.endswith("ORDER BY created ASC")
    assert " AND " in jql


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------


def test_issue_to_event():
    issue = {
        "key": "DEV-1",
        "fields": {
            "summary": "Crash on boot",
            "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "boom"}]}]},
            "issuetype": {"name": "Bug"},
            "status": {"name": "Open"},
            "priority": {"name": "High"},
            "resolution": None,
            "labels": ["backend"],
            "components": [{"name": "api"}],
            "assignee": {"displayName": "Rob Miller"},
            "reporter": {"displayName": "Alice"},
            "created": "2026-01-01T00:00:00.000+0000",
            "updated": "2026-02-01T00:00:00.000+0000",
            "parent": {"key": "DEV-0"},
        },
    }
    ev = issue_to_event(issue, site_url=SITE, comments=[{"author": "Bob", "body": "seen it", "created": "x"}])
    assert ev.op == "upsert"
    assert ev.key == "jira:DEV-1"
    assert ev.data_format == "json"
    assert ev.payload["summary"] == "Crash on boot"
    assert ev.payload["description"] == "boom"
    assert ev.payload["type"] == "Bug"
    assert ev.payload["status"] == "Open"
    assert ev.payload["resolution"] == ""
    assert ev.payload["components"] == ["api"]
    assert ev.payload["assignee"] == "Rob Miller"
    assert ev.payload["parent"] == "DEV-0"
    assert ev.payload["url"] == f"{SITE}/browse/DEV-1"
    assert ev.payload["comments"][0]["author"] == "Bob"
    assert ev.source_meta == {
        "source": "jira",
        "project": "DEV",
        "key": "DEV-1",
        "url": f"{SITE}/browse/DEV-1",
        "updated": "2026-02-01T00:00:00.000+0000",
    }


def test_page_to_event():
    page = {
        "id": 33046,
        "title": "Runbook",
        "spaceId": "131275",
        "version": {"number": 4, "createdAt": "2026-03-01T00:00:00Z", "authorId": "acc9"},
        "createdAt": "2025-01-01T00:00:00Z",
        "_links": {"webui": "/spaces/ENG/pages/33046/Runbook"},
    }
    ev = page_to_event(
        page,
        site_url=SITE,
        space_key="ENG",
        body_text="do the thing",
        labels=["runbook"],
        comments=[{"author": "acc2", "body": "nice"}],
    )
    assert ev.key == "confluence:33046"
    assert ev.payload["title"] == "Runbook"
    assert ev.payload["space"] == "ENG"
    assert ev.payload["body"] == "do the thing"
    assert ev.payload["version"] == 4
    assert ev.payload["labels"] == ["runbook"]
    assert ev.payload["url"] == f"{SITE}/wiki/spaces/ENG/pages/33046/Runbook"
    assert ev.source_meta["source"] == "confluence"
    assert ev.source_meta["spaceId"] == "131275"


# ---------------------------------------------------------------------------
# Rate-limit backoff
# ---------------------------------------------------------------------------


def _resp(headers: dict) -> httpx.Response:
    return httpx.Response(status_code=429, headers=headers, content=b"")


def test_retry_after_numeric():
    assert _retry_after(_resp({"Retry-After": "12"})) == 12.0


def test_retry_after_missing_defaults():
    assert _retry_after(_resp({})) == 60.0


def test_retry_after_http_date_defaults():
    assert _retry_after(_resp({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})) == 60.0
