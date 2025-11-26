"""Data normalization using parser registry"""

from typing import Any, Optional, Tuple, Dict, List

from .parsers import (
    BaseFormatParser,
    ParseResult,
    JSONParser,
    YAMLParser,
    TOMLParser,
    XMLParser,
    CSVParser,
    MarkdownParser,
    CodeParser,
    PlainTextParser,
)


class DataNormalizer:
    """
    Normalizes data from various formats using a parser registry.

    The normalizer tries parsers in priority order until one succeeds.
    Each parser implements format detection and normalization logic.
    """

    def __init__(self):
        """Initialize parser registry"""

        # Register all parsers
        self.parsers: List[BaseFormatParser] = [
            JSONParser(),
            YAMLParser(),
            TOMLParser(),
            XMLParser(),
            CSVParser(),
            MarkdownParser(),
            CodeParser(),
            PlainTextParser(),  # Fallback (always succeeds)
        ]

        # Sort by priority (lowest first)
        self.parsers.sort(key=lambda p: p.priority)

    def normalize(
        self,
        raw_data: Any,
        format_hint: Optional[str] = None
    ) -> Tuple[Dict[str, Any], str, bool]:
        """
        Normalize data from any format using parser registry.

        Args:
            raw_data: Raw input data
            format_hint: Optional format hint ("json", "yaml", etc.)

        Returns:
            Tuple of (normalized_data, detected_format, is_structured)

        Raises:
            ValueError: If no parser can handle the data
        """

        # Try parsers in priority order
        for parser in self.parsers:
            # Check if parser can handle this data
            if parser.can_parse(raw_data, format_hint):
                # Try parsing
                result = parser.parse(raw_data)

                if result.success:
                    return (
                        result.normalized_data,
                        result.format_name,
                        result.is_structured
                    )

        # This should never happen because PlainTextParser always succeeds
        raise ValueError("No parser could handle the data")

    def generate_embedding_text(
        self,
        data_key: str,
        normalized_data: Dict[str, Any],
        is_structured: bool
    ) -> str:
        """
        Generate text for embedding based on data type.

        Args:
            data_key: Data identifier
            normalized_data: Normalized data dict
            is_structured: Whether data is structured

        Returns:
            Text to embed for semantic matching
        """

        if is_structured:
            # For structured data: prioritize actual content over structure
            import json
            try:
                # Remove metadata fields for cleaner representation
                data_copy = {k: v for k, v in normalized_data.items()
                           if not k.startswith('_') and k not in ['content_type', 'structure', 'schema']}

                # Convert to readable text format instead of JSON
                content_parts = []
                for key, value in data_copy.items():
                    if isinstance(value, (list, dict)):
                        value_str = json.dumps(value, ensure_ascii=False)
                    else:
                        value_str = str(value)
                    content_parts.append(f"{key}: {value_str}")

                content = " | ".join(content_parts)

            except (TypeError, ValueError) as e:
                print(f"[DataNormalizer] Warning: Failed to serialize data: {type(e).__name__}, using str() fallback")
                content = str(normalized_data)

            # For chunks, focus on content. Only include data_key if it's meaningful
            # Strip array indices and path separators that add noise
            clean_key = data_key.split('[')[0].split('.')[-1] if '[' in data_key or '.' in data_key else data_key

            # Truncate if needed
            max_content_chars = 500
            if len(content) > max_content_chars:
                content = content[:max_content_chars] + "..."

            # Put content first for better matching
            if clean_key and clean_key != data_key:
                return f"{content} ({clean_key})"
            else:
                return f"{data_key}: {content}"

        else:
            # For unstructured data: embed data_key + actual content
            content = normalized_data.get("content", "")
            # Truncate long text for embedding
            max_chars = 500
            if len(content) > max_chars:
                content = content[:max_chars] + "..."

            return f"{data_key}: {content}"

    def _extract_field_paths(
        self,
        data: Dict[str, Any],
        prefix: str = "",
        max_depth: int = 5
    ) -> List[str]:
        """
        Extract flattened field paths from nested dict.

        Example:
            {"user": {"name": "John", "age": 30}}
            → ["user.name", "user.age"]
        """
        if max_depth <= 0:
            return []

        fields = []

        for key, value in data.items():
            # Skip metadata fields
            if key.startswith("_") or key.startswith("@"):
                continue

            # Skip special fields
            if key in ["content", "content_type", "structure", "schema", "records"]:
                continue

            field_path = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict) and value:
                # Recurse into nested dicts
                fields.extend(self._extract_field_paths(value, field_path, max_depth - 1))
            elif isinstance(value, list) and value:
                # For arrays, note it's an array
                if isinstance(value[0], dict):
                    fields.append(f"{field_path}[]")
                else:
                    fields.append(field_path)
            else:
                fields.append(field_path)

        return fields
