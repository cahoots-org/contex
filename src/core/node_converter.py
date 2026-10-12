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

        A generic or unknown hint falls back to content sniffing. Text no parser
        accepts is split as plain text; bytes no parser accepts fail.
        """
        parsers = [p for p in self.parsers if p.format_name == format_hint]
        if format_hint in GENERIC_FORMATS or not parsers:
            parsers = [p for p in self.parsers if p.can_parse(data, format_hint)]
        result = None
        for parser in parsers:
            try:
                result = (
                    parser.parse(data, data_key=data_key)
                    if parser.needs_data_key
                    else parser.parse(data)
                )
            except Exception as e:  # a failed parser falls through to the next one
                result = ParseResult(nodes=[], format_name=parser.format_name, success=False, error=str(e))
            if result.success:
                return result
        if isinstance(data, (bytes, bytearray)):
            return result or ParseResult(
                nodes=[], format_name="binary", success=False, error="unrecognised binary data"
            )
        return PlainTextNodeParser().parse(str(data))
