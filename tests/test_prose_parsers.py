"""Prose parsers merge blank-line paragraphs into embedding-sized chunks instead of one node each."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.node_parsers import MarkdownNodeParser, PlainTextNodeParser

ARTICLE = "\n\n".join(f"Paragraph {i} of a news article body." for i in range(8))


def test_short_article_is_one_node():
    nodes = PlainTextNodeParser().parse(ARTICLE).nodes
    assert [n.content for n in nodes] == [ARTICLE]


def test_long_article_splits_at_paragraph_boundaries():
    para = "x" * 900
    nodes = PlainTextNodeParser().parse(f"{para}\n\n{para}\n\n{para}").nodes
    assert [n.content for n in nodes] == [para, para, para]


def test_markdown_section_paragraphs_merge():
    nodes = MarkdownNodeParser().parse(f"# Title\n\n{ARTICLE}").nodes
    assert [n.content for n in nodes] == ["Title", ARTICLE]
