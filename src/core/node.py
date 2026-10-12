"""
Node-based data model for format-agnostic representation.

A Node represents a semantic unit of data that can be:
- A JSON object or array element
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
    PRIMITIVE = "primitive"     # String, number, boolean, null

    # Text types
    DOCUMENT = "document"       # Full document
    SECTION = "section"         # Major section (e.g., markdown heading section)
    PARAGRAPH = "paragraph"     # Paragraph of text
    SENTENCE = "sentence"       # Single sentence

    # Code types
    FUNCTION = "function"       # Function definition
    CLASS = "class"            # Class definition
    METHOD = "method"          # Class method

    # Markup types
    HEADING = "heading"        # Heading/title
    CODE_BLOCK = "code_block" # Code block

    # Tabular types
    ROW = "row"               # CSV/table row


@dataclass
class Node:
    """
    A semantic unit of data.

    Attributes:
        path: Hierarchical path (e.g., "people[1]", "section_2.paragraph_3")
        content: The actual data (can be dict, str, number, list, etc.)
        node_type: Type of node
        metadata: For code, the names the node defines (defs) and references (refs)
    """
    path: str
    content: Any
    node_type: NodeType
    metadata: Dict[str, Any] = field(default_factory=dict)

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
            text_parts.append(" | ".join(f"{k}: {v}" for k, v in self.content.items()))
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
