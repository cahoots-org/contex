"""Flatten Atlassian rich text into plain text for embedding.

Jira issue descriptions and comments arrive as **Atlassian Document Format**
(ADF) — a nested JSON node tree. Confluence page bodies arrive as **storage
format** — XHTML with ``ac:``/``ri:`` macro tags. Both carry the semantic
content Contex should embed, so each is flattened to readable plain text while
preserving structure cues (paragraphs, list items, headings) as newlines.

Kept to the standard library (``html.parser``) so the connector adds no
dependency beyond ``httpx``.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# Whitespace normalization (shared)
# ---------------------------------------------------------------------------

_TRAILING_WS = re.compile(r"[ \t]+(\n)")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_MANY_SPACES = re.compile(r"[ \t]{2,}")


def _normalize(text: str) -> str:
    """Collapse redundant whitespace and trim, keeping paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MANY_SPACES.sub(" ", text)
    text = _TRAILING_WS.sub(r"\1", text)
    text = _MANY_NEWLINES.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# ADF (Jira) -> text
# ---------------------------------------------------------------------------

# Block-level ADF nodes that should end with a line break.
_ADF_BLOCKS = frozenset(
    {
        "paragraph",
        "heading",
        "blockquote",
        "codeBlock",
        "listItem",
        "tableRow",
        "rule",
        "panel",
        "mediaSingle",
        "mediaGroup",
    }
)


def adf_to_text(node: object) -> str:
    """Flatten an Atlassian Document Format value to plain text.

    Accepts the document dict, a node dict, a list of nodes, ``None``, or a bare
    string (older Jira descriptions). Unknown node types degrade to their
    concatenated children, so new ADF nodes never drop text silently.
    """
    return _normalize(_walk_adf(node))


def _walk_adf(node: object) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_walk_adf(child) for child in node)
    if not isinstance(node, dict):
        return ""

    ntype = node.get("type")
    if ntype == "text":
        return node.get("text", "")
    if ntype == "hardBreak":
        return "\n"
    if ntype == "mention":
        return (node.get("attrs") or {}).get("text", "")
    if ntype == "emoji":
        attrs = node.get("attrs") or {}
        return attrs.get("text") or attrs.get("shortName", "")
    if ntype == "inlineCard":
        return (node.get("attrs") or {}).get("url", "")

    inner = _walk_adf(node.get("content", []))
    if ntype == "listItem":
        return f"- {inner}\n"
    if ntype in _ADF_BLOCKS:
        return f"{inner}\n"
    return inner


# ---------------------------------------------------------------------------
# Storage-format HTML (Confluence) -> text
# ---------------------------------------------------------------------------

_HTML_BLOCKS = frozenset(
    {
        "p",
        "br",
        "div",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "table",
    }
)
# Confluence storage tags whose *content* is layout noise, not prose.
_HTML_SKIP = frozenset({"ac:parameter", "ri:attachment", "ri:url", "ri:page"})


class _TextExtractor(HTMLParser):
    """Collect text, turning block-level tags into newlines and ``li`` into bullets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _HTML_SKIP:
            self._skip_depth += 1
            return
        if tag in _HTML_BLOCKS:
            self._parts.append("\n")
        if tag == "li":
            self._parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag in _HTML_BLOCKS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def storage_html_to_text(html: str) -> str:
    """Flatten Confluence storage-format XHTML to plain text."""
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return _normalize(parser.text())
