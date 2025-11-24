"""YAML format parser"""

import re
import yaml
from typing import Any, Optional

from .base import BaseFormatParser, ParseResult


class YAMLParser(BaseFormatParser):
    """Parser for YAML data"""

    @property
    def format_name(self) -> str:
        return "yaml"

    @property
    def priority(self) -> int:
        return 1  # High priority (after JSON)

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        """Check if data is YAML"""

        # If hint says it's YAML, try it
        if format_hint == "yaml":
            return True

        # Only parse strings
        if not isinstance(data, str):
            return False

        # Reject if it looks like plain prose
        # Plain text has multiple sentences with periods
        sentences = data.count('.')
        if sentences >= 2:
            # Check if it has natural language patterns
            prose_patterns = [
                r'\b(the|a|an|we|they|this|that|these|those)\b',
                r'\b(discussed|decided|should|would|could|will)\b',
            ]
            prose_matches = sum(1 for p in prose_patterns if re.search(p, data, re.IGNORECASE))
            if prose_matches >= 2:
                return False

        # Look for YAML structural patterns
        yaml_patterns = [
            r'^\s*[\w-]+:\s*\S',  # key: value on same line
            r'^\s*-\s+[\w-]+:',    # list item with key
            r'^\s*[\w-]+:\s*$',    # key: with value on next line
        ]

        has_yaml_pattern = any(re.search(p, data, re.MULTILINE) for p in yaml_patterns)
        if not has_yaml_pattern:
            return False

        # Try parsing as YAML
        try:
            obj = yaml.safe_load(data)
            # Only handle if result is a dict
            if not isinstance(obj, dict):
                return False

            # Accept if it has multiple keys OR nested structure
            if len(obj) >= 2:
                return True

            # Check if single key has nested dict/list value
            if len(obj) == 1:
                value = next(iter(obj.values()))
                if isinstance(value, (dict, list)):
                    return True

            return False
        except:
            return False

    def parse(self, data: Any) -> ParseResult:
        """Parse YAML data"""

        try:
            if not isinstance(data, str):
                return ParseResult(
                    success=False,
                    error="YAML data must be a string"
                )

            # Parse YAML
            parsed = yaml.safe_load(data)

            # Only accept dict results
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
                    error=f"YAML parsed to {type(parsed).__name__}, expected dict"
                )

        except yaml.YAMLError as e:
            return ParseResult(
                success=False,
                error=f"YAML parse error: {str(e)}"
            )
        except Exception as e:
            return ParseResult(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
