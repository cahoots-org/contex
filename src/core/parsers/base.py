"""Base parser interface for multi-format data ingestion"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ParseResult:
    """Result from parsing data"""

    success: bool
    normalized_data: Optional[Dict[str, Any]] = None
    format_name: str = "unknown"
    is_structured: bool = False
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class BaseFormatParser(ABC):
    """
    Base class for all format parsers.

    Each parser is responsible for:
    1. Detecting if it can handle the input
    2. Parsing/normalizing the data
    3. Extracting structure information for embeddings
    """

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Name of the format this parser handles (e.g., 'json', 'yaml', 'xml')"""
        pass

    @property
    @abstractmethod
    def priority(self) -> int:
        """
        Priority for parser selection (lower = higher priority).

        Used when multiple parsers might match:
        - 0-9: High priority (structured formats like JSON, YAML)
        - 10-19: Medium priority (semi-structured like XML, CSV)
        - 20-29: Low priority (code, markdown)
        - 30+: Fallback (plain text)
        """
        pass

    @abstractmethod
    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        """
        Check if this parser can handle the given data.

        Args:
            data: Raw input data
            format_hint: Optional format hint from user

        Returns:
            True if this parser should attempt parsing
        """
        pass

    @abstractmethod
    def parse(self, data: Any) -> ParseResult:
        """
        Parse and normalize the data.

        Args:
            data: Raw input data

        Returns:
            ParseResult with normalized data or error
        """
        pass

    def extract_field_paths(
        self,
        data: Dict[str, Any],
        prefix: str = "",
        max_depth: int = 5
    ) -> list[str]:
        """
        Extract flattened field paths from nested dict (useful for structured data).

        Args:
            data: Dictionary to extract paths from
            prefix: Current path prefix
            max_depth: Maximum nesting depth to traverse

        Returns:
            List of field paths (e.g., ["user.name", "user.email", "settings.theme"])
        """
        if max_depth <= 0:
            return []

        fields = []

        for key, value in data.items():
            # Skip internal metadata fields
            if key.startswith("_"):
                continue

            field_path = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict) and value:
                # Recurse into nested dicts
                fields.extend(
                    self.extract_field_paths(value, field_path, max_depth - 1)
                )
            elif isinstance(value, list) and value:
                # For arrays, note it's an array
                if isinstance(value[0], dict):
                    fields.append(f"{field_path}[]")
                else:
                    fields.append(field_path)
            else:
                fields.append(field_path)

        return fields

    def generate_description(
        self,
        data_key: str,
        normalized_data: Dict[str, Any],
        is_structured: bool
    ) -> str:
        """
        Generate description for embedding.

        Args:
            data_key: Data identifier
            normalized_data: Normalized data dict
            is_structured: Whether data is structured

        Returns:
            Description text for embedding
        """
        if is_structured:
            # For structured data: include field paths
            fields = self.extract_field_paths(normalized_data)
            if fields:
                fields_text = ", ".join(fields[:20])  # Limit to first 20
                return f"{data_key} with fields: {fields_text}"
            else:
                return data_key
        else:
            # For unstructured data: include content preview
            content = normalized_data.get("content", "")
            if isinstance(content, str):
                # Truncate long content
                max_chars = 500
                if len(content) > max_chars:
                    content = content[:max_chars] + "..."
                return f"{data_key}: {content}"
            else:
                return data_key
