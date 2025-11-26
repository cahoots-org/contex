"""
NodeConverter: Bidirectional conversion between formats and Nodes.

Handles parsing any format into Nodes and reconstructing from Nodes back to any format.
"""

from typing import Any, List, Optional, Type
from .node import Node, ParseResult
from .node_parsers import (
    BaseNodeParser,
    JSONNodeParser,
    YAMLNodeParser,
    PlainTextNodeParser,
    MarkdownNodeParser,
    CSVNodeParser,
)


class NodeConverter:
    """
    Central converter for parsing data into Nodes and reconstructing back.

    Usage:
        converter = NodeConverter()

        # Parse to nodes
        result = converter.parse(data, format_hint="json")
        nodes = result.nodes

        # Convert nodes to different format
        json_output = converter.to_json(nodes)
        yaml_output = converter.to_yaml(nodes)
        text_output = converter.to_text(nodes)
    """

    def __init__(self):
        """Initialize with all available parsers"""
        self.parsers: List[BaseNodeParser] = [
            JSONNodeParser(),
            YAMLNodeParser(),
            CSVNodeParser(),
            MarkdownNodeParser(),
            PlainTextNodeParser(),  # Fallback
        ]

        # Sort by priority
        self.parsers.sort(key=lambda p: p.priority)

    def parse(self, data: Any, format_hint: Optional[str] = None) -> ParseResult:
        """
        Parse data into nodes using appropriate parser.

        Args:
            data: Raw data to parse
            format_hint: Optional hint about format ("json", "yaml", etc.)

        Returns:
            ParseResult with nodes and metadata
        """
        # Try parsers in priority order
        for parser in self.parsers:
            if parser.can_parse(data, format_hint):
                result = parser.parse(data)
                if result.success:
                    return result

        # Fallback: treat as plain text
        return PlainTextNodeParser().parse(str(data))

    def to_json(self, nodes: List[Node]) -> dict:
        """Convert nodes to JSON/dict"""
        return JSONNodeParser().reconstruct(nodes, target_format=None)

    def to_json_string(self, nodes: List[Node]) -> str:
        """Convert nodes to JSON string"""
        return JSONNodeParser().reconstruct(nodes, target_format="string")

    def to_yaml(self, nodes: List[Node]) -> str:
        """Convert nodes to YAML string"""
        return YAMLNodeParser().reconstruct(nodes, target_format="string")

    def to_text(self, nodes: List[Node]) -> str:
        """Convert nodes to plain text"""
        return PlainTextNodeParser().reconstruct(nodes, target_format=None)

    def to_markdown(self, nodes: List[Node]) -> str:
        """Convert nodes to Markdown"""
        return MarkdownNodeParser().reconstruct(nodes, target_format=None)

    def to_csv(self, nodes: List[Node]) -> str:
        """Convert nodes to CSV"""
        return CSVNodeParser().reconstruct(nodes, target_format=None)

    def to_format(self, nodes: List[Node], format_name: str) -> Any:
        """
        Convert nodes to specified format.

        Args:
            nodes: List of nodes to convert
            format_name: Target format ("json", "yaml", "text", "markdown", "csv")

        Returns:
            Converted data in target format
        """
        format_map = {
            "json": self.to_json,
            "json_string": self.to_json_string,
            "yaml": self.to_yaml,
            "text": self.to_text,
            "markdown": self.to_markdown,
            "csv": self.to_csv,
        }

        converter = format_map.get(format_name)
        if not converter:
            raise ValueError(f"Unknown format: {format_name}")

        return converter(nodes)

    def get_embedding_texts(self, nodes: List[Node]) -> List[str]:
        """
        Get text representations of nodes for embedding.

        Args:
            nodes: List of nodes

        Returns:
            List of text strings, one per node
        """
        return [node.get_text_content() for node in nodes]
