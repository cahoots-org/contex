"""NodeConverter: parse any supported format into Nodes."""

from typing import Any, List, Optional
from .node import ParseResult
from .node_parsers import (
    BaseNodeParser,
    JSONNodeParser,
    YAMLNodeParser,
    PlainTextNodeParser,
    MarkdownNodeParser,
    CSVNodeParser,
    PDFNodeParser,
    ImageNodeParser,
    DOCXNodeParser,
    CodeNodeParser,
)


class NodeConverter:
    """Picks a parser for the data and parses it into Nodes."""

    def __init__(self):
        """Initialize with all available parsers"""
        self.parsers: List[BaseNodeParser] = [
            CodeNodeParser(),
            PDFNodeParser(),
            ImageNodeParser(),
            DOCXNodeParser(),
            JSONNodeParser(),
            YAMLNodeParser(),
            CSVNodeParser(),
            MarkdownNodeParser(),
            PlainTextNodeParser(),  # Fallback
        ]

        # Sort by priority
        self.parsers.sort(key=lambda p: p.priority)

    def parse(
        self, data: Any, format_hint: Optional[str] = None, data_key: Optional[str] = None
    ) -> ParseResult:
        """
        Parse data into nodes using appropriate parser.

        Args:
            data: Raw data to parse
            format_hint: Optional hint about format ("json", "yaml", etc.)
            data_key: Optional source key; parsers that need the file extension to
                pick a language (CodeNodeParser) read it.

        Returns:
            ParseResult with nodes and metadata
        """
        # Try parsers in priority order
        for parser in self.parsers:
            if parser.can_parse(data, format_hint):
                result = (
                    parser.parse(data, data_key=data_key)
                    if parser.needs_data_key
                    else parser.parse(data)
                )
                if result.success:
                    return result

        # Fallback: treat as plain text
        return PlainTextNodeParser().parse(str(data))
