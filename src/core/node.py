"""
Node-based data model for format-agnostic representation.

A Node represents a semantic unit of data that can be:
- A JSON object or array element
- An XML/HTML tag
- A paragraph or sentence
- A CSV row
- A code function or class
- Any other meaningful chunk of information
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


class NodeType(str, Enum):
    """Types of nodes"""
    # Structured types
    OBJECT = "object"           # Dictionary/object/record
    ARRAY = "array"             # List/array (usually contains other nodes)
    PRIMITIVE = "primitive"     # String, number, boolean, null

    # Text types
    DOCUMENT = "document"       # Full document
    SECTION = "section"         # Major section (e.g., markdown heading section)
    PARAGRAPH = "paragraph"     # Paragraph of text
    SENTENCE = "sentence"       # Single sentence
    LINE = "line"              # Single line (logs, code)

    # Code types
    FUNCTION = "function"       # Function definition
    CLASS = "class"            # Class definition
    METHOD = "method"          # Class method

    # Markup types
    ELEMENT = "element"        # XML/HTML element
    HEADING = "heading"        # Heading/title
    LIST = "list"             # Ordered or unordered list
    LIST_ITEM = "list_item"   # List item
    CODE_BLOCK = "code_block" # Code block

    # Tabular types
    ROW = "row"               # CSV/table row
    CELL = "cell"             # Table cell


@dataclass
class Node:
    """
    A semantic unit of data.

    Attributes:
        path: Hierarchical path (e.g., "people[1]", "section_2.paragraph_3")
        content: The actual data (can be dict, str, number, list, etc.)
        node_type: Type of node
        metadata: Additional context (format, language, size, etc.)
    """
    path: str
    content: Any
    node_type: NodeType
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "path": self.path,
            "content": self.content,
            "node_type": self.node_type.value,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Node":
        """Create from dictionary"""
        return cls(
            path=data["path"],
            content=data["content"],
            node_type=NodeType(data["node_type"]),
            metadata=data.get("metadata", {})
        )

    def get_text_content(self) -> str:
        """
        Get text representation of content for embedding.

        Includes path segments (property names) to make them searchable.

        Returns:
            String representation of the content with path context
        """
        # Extract property names from path (e.g., "team.people[0]" -> ["team", "people"])
        path_parts = []
        if self.path:
            # Remove array indices and split by dots
            path_cleaned = self.path.replace('[', '.').replace(']', '')
            path_parts = [p for p in path_cleaned.split('.') if p and not p.isdigit()]

        # Build searchable text
        text_parts = []

        # Add path context (property names)
        if path_parts:
            text_parts.append(" ".join(path_parts))

        # Add content
        if isinstance(self.content, dict):
            # Convert dict to readable key: value format
            content_parts = []
            for k, v in self.content.items():
                if isinstance(v, (dict, list)):
                    v_str = str(v)
                else:
                    v_str = str(v)
                content_parts.append(f"{k}: {v_str}")
            text_parts.append(" | ".join(content_parts))
        elif isinstance(self.content, list):
            text_parts.append(", ".join(str(item) for item in self.content))
        else:
            text_parts.append(str(self.content))

        return " | ".join(text_parts)

    def __repr__(self) -> str:
        content_preview = str(self.content)[:50]
        if len(str(self.content)) > 50:
            content_preview += "..."
        return f"Node(path='{self.path}', type={self.node_type.value}, content='{content_preview}')"


@dataclass
class ParseResult:
    """Result of parsing data into nodes"""
    nodes: List[Node]
    format_name: str
    success: bool
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
