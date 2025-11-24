"""TOML format parser"""

from typing import Any, Optional

from .base import BaseFormatParser, ParseResult

try:
    import toml
    TOML_AVAILABLE = True
except ImportError:
    TOML_AVAILABLE = False


class TOMLParser(BaseFormatParser):
    """Parser for TOML data"""

    @property
    def format_name(self) -> str:
        return "toml"

    @property
    def priority(self) -> int:
        return 2  # High priority

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        """Check if data is TOML"""

        if not TOML_AVAILABLE:
            return False

        # If hint says it's TOML, try it
        if format_hint == "toml":
            return True

        # Only parse strings
        if not isinstance(data, str):
            return False

        # Try parsing as TOML
        try:
            obj = toml.loads(data)
            return isinstance(obj, dict)
        except:
            return False

    def parse(self, data: Any) -> ParseResult:
        """Parse TOML data"""

        if not TOML_AVAILABLE:
            return ParseResult(
                success=False,
                error="TOML library not installed"
            )

        try:
            if not isinstance(data, str):
                return ParseResult(
                    success=False,
                    error="TOML data must be a string"
                )

            # Parse TOML
            parsed = toml.loads(data)

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
                    error=f"TOML parsed to {type(parsed).__name__}, expected dict"
                )

        except toml.TomlDecodeError as e:
            return ParseResult(
                success=False,
                error=f"TOML parse error: {str(e)}"
            )
        except Exception as e:
            return ParseResult(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
