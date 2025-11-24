"""Plain text parser (fallback for unstructured data)"""

from typing import Any, Optional

from .base import BaseFormatParser, ParseResult


class PlainTextParser(BaseFormatParser):
    """Parser for plain text (fallback)"""

    @property
    def format_name(self) -> str:
        return "text"

    @property
    def priority(self) -> int:
        return 100  # Lowest priority (always matches as fallback)

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        """Always returns True (fallback parser)"""

        # If hint says it's text, definitely try it
        if format_hint == "text":
            return True

        # Accept any string
        if isinstance(data, str):
            return True

        # Convert anything else to string
        return True

    def parse(self, data: Any) -> ParseResult:
        """Convert data to plain text"""

        try:
            # Convert to string
            text = str(data) if not isinstance(data, str) else data

            # Wrap in standard format
            normalized_data = {
                "content": text,
                "content_type": "text"
            }

            return ParseResult(
                success=True,
                normalized_data=normalized_data,
                format_name=self.format_name,
                is_structured=False
            )

        except Exception as e:
            return ParseResult(
                success=False,
                error=f"Text conversion error: {str(e)}"
            )
