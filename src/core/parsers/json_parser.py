"""JSON format parser"""

import json
from typing import Any, Optional

from .base import BaseFormatParser, ParseResult


class JSONParser(BaseFormatParser):
    """Parser for JSON data"""

    @property
    def format_name(self) -> str:
        return "json"

    @property
    def priority(self) -> int:
        return 0  # Highest priority

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        """Check if data is JSON"""

        # If hint says it's JSON, try it
        if format_hint == "json":
            return True

        # Already a dict = JSON-like object
        if isinstance(data, dict):
            return True

        # Try parsing as JSON string
        if isinstance(data, str):
            try:
                obj = json.loads(data)
                return isinstance(obj, dict)
            except:
                return False

        return False

    def parse(self, data: Any) -> ParseResult:
        """Parse JSON data"""

        try:
            # Already a dict
            if isinstance(data, dict):
                return ParseResult(
                    success=True,
                    normalized_data=data,
                    format_name=self.format_name,
                    is_structured=True
                )

            # Parse JSON string
            if isinstance(data, str):
                parsed = json.loads(data)
                if isinstance(parsed, dict):
                    return ParseResult(
                        success=True,
                        normalized_data=parsed,
                        format_name=self.format_name,
                        is_structured=True
                    )
                else:
                    return ParseResult(
                        success=False,
                        error="JSON parsed but not a dict"
                    )

            return ParseResult(
                success=False,
                error="Data is not JSON"
            )

        except json.JSONDecodeError as e:
            return ParseResult(
                success=False,
                error=f"JSON parse error: {str(e)}"
            )
        except Exception as e:
            return ParseResult(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
