"""NodeConverter: parse any supported format into Nodes."""

from typing import Any, List, Optional
from .node import ParseResult
from .node_parsers import (
    GENERIC_FORMATS,
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
        """Parse data with the hinted format's parser, else the first that fits.

        A generic or unknown hint falls back to content sniffing; whatever no
        parser accepts is split as plain text.
        """
        parsers = [p for p in self.parsers if p.format_name == format_hint]
        if format_hint in GENERIC_FORMATS or not parsers:
            parsers = [p for p in self.parsers if p.can_parse(data, format_hint)]
        for parser in parsers:
            result = (
                parser.parse(data, data_key=data_key)
                if parser.needs_data_key
                else parser.parse(data)
            )
            if result.success:
                return result
        return PlainTextNodeParser().parse(str(data))
