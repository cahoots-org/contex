"""Markdown format parser with structure extraction"""

import re
from typing import Any, Optional, Dict, List

from .base import BaseFormatParser, ParseResult


class MarkdownParser(BaseFormatParser):
    """Parser for Markdown documents"""

    @property
    def format_name(self) -> str:
        return "markdown"

    @property
    def priority(self) -> int:
        return 20  # Low priority (tries after structured formats)

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        """Check if data is Markdown"""

        # If hint says it's Markdown, try it
        if format_hint in ["markdown", "md"]:
            return True

        # Only parse strings
        if not isinstance(data, str):
            return False

        # Look for markdown patterns
        markdown_patterns = [
            r'^#{1,6}\s',  # Headers
            r'^\*\*[^*]+\*\*',  # Bold
            r'^\*[^*]+\*',  # Italic
            r'^\[.+\]\(.+\)',  # Links
            r'^```',  # Code blocks
            r'^\-\s',  # Lists
            r'^\d+\.\s',  # Numbered lists
        ]

        for pattern in markdown_patterns:
            if re.search(pattern, data, re.MULTILINE):
                return True

        return False

    def parse(self, data: Any) -> ParseResult:
        """Parse Markdown and extract structure"""

        try:
            if not isinstance(data, str):
                return ParseResult(
                    success=False,
                    error="Markdown data must be a string"
                )

            # Extract structure
            structure = self._extract_structure(data)

            # Create normalized representation
            normalized_data = {
                "content": data,
                "content_type": "markdown",
                "structure": structure,
                **self._extract_metadata(data, structure)
            }

            return ParseResult(
                success=True,
                normalized_data=normalized_data,
                format_name=self.format_name,
                is_structured=False,  # Consider markdown unstructured
                metadata=structure
            )

        except Exception as e:
            return ParseResult(
                success=False,
                error=f"Markdown parse error: {str(e)}"
            )

    def _extract_structure(self, markdown: str) -> Dict[str, Any]:
        """Extract structural elements from markdown"""

        structure = {
            "headings": self._extract_headings(markdown),
            "links": self._extract_links(markdown),
            "code_blocks": self._extract_code_blocks(markdown),
            "lists": self._extract_lists(markdown)
        }

        return structure

    def _extract_headings(self, markdown: str) -> List[Dict[str, Any]]:
        """Extract headings with hierarchy"""

        headings = []
        heading_pattern = r'^(#{1,6})\s+(.+)$'

        for line in markdown.split('\n'):
            match = re.match(heading_pattern, line.strip())
            if match:
                level = len(match.group(1))
                text = match.group(2)
                headings.append({"level": level, "text": text})

        return headings

    def _extract_links(self, markdown: str) -> List[Dict[str, str]]:
        """Extract links [text](url)"""

        links = []
        link_pattern = r'\[([^\]]+)\]\(([^\)]+)\)'

        for match in re.finditer(link_pattern, markdown):
            links.append({
                "text": match.group(1),
                "url": match.group(2)
            })

        return links

    def _extract_code_blocks(self, markdown: str) -> List[Dict[str, str]]:
        """Extract fenced code blocks"""

        code_blocks = []
        code_block_pattern = r'```(\w*)\n(.*?)\n```'

        for match in re.finditer(code_block_pattern, markdown, re.DOTALL):
            language = match.group(1) or "text"
            code = match.group(2)
            code_blocks.append({
                "language": language,
                "code": code
            })

        return code_blocks

    def _extract_lists(self, markdown: str) -> Dict[str, int]:
        """Count list items"""

        unordered_items = len(re.findall(r'^\s*[-*+]\s', markdown, re.MULTILINE))
        ordered_items = len(re.findall(r'^\s*\d+\.\s', markdown, re.MULTILINE))

        return {
            "unordered_items": unordered_items,
            "ordered_items": ordered_items
        }

    def _extract_metadata(self, markdown: str, structure: Dict[str, Any]) -> Dict[str, Any]:
        """Extract high-level metadata"""

        metadata = {}

        # Title (first heading or first line)
        headings = structure.get("headings", [])
        if headings:
            metadata["title"] = headings[0]["text"]
        else:
            first_line = markdown.split('\n')[0] if markdown else ""
            metadata["title"] = first_line[:100]

        # Summary (first paragraph)
        paragraphs = [p.strip() for p in re.split(r'\n\n+', markdown) if p.strip()]
        if paragraphs:
            # Skip first if it's a heading
            start_idx = 1 if paragraphs[0].startswith('#') else 0
            if start_idx < len(paragraphs):
                metadata["summary"] = paragraphs[start_idx][:200]

        # Stats
        metadata["heading_count"] = len(headings)
        metadata["link_count"] = len(structure.get("links", []))
        metadata["code_block_count"] = len(structure.get("code_blocks", []))

        return metadata
