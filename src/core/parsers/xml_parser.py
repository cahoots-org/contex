"""XML format parser"""

import xml.etree.ElementTree as ET
from typing import Any, Optional, Dict

from .base import BaseFormatParser, ParseResult


class XMLParser(BaseFormatParser):
    """Parser for XML data"""

    @property
    def format_name(self) -> str:
        return "xml"

    @property
    def priority(self) -> int:
        return 10  # Medium priority

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        """Check if data is XML"""

        # If hint says it's XML, try it
        if format_hint == "xml":
            return True

        # Only parse strings
        if not isinstance(data, str):
            return False

        # Check for XML-like content
        data_stripped = data.strip()
        if not (data_stripped.startswith('<') and data_stripped.endswith('>')):
            return False

        # Try parsing as XML
        try:
            ET.fromstring(data)
            return True
        except:
            return False

    def parse(self, data: Any) -> ParseResult:
        """Parse XML data and convert to dict"""

        try:
            if not isinstance(data, str):
                return ParseResult(
                    success=False,
                    error="XML data must be a string"
                )

            # Parse XML
            root = ET.fromstring(data)

            # Convert XML to dict
            normalized_data = self._xml_to_dict(root)

            return ParseResult(
                success=True,
                normalized_data=normalized_data,
                format_name=self.format_name,
                is_structured=True,
                metadata={"root_tag": root.tag}
            )

        except ET.ParseError as e:
            return ParseResult(
                success=False,
                error=f"XML parse error: {str(e)}"
            )
        except Exception as e:
            return ParseResult(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )

    def _xml_to_dict(self, element: ET.Element) -> Dict[str, Any]:
        """
        Convert XML element to dictionary.

        Strategy:
        - Element tag becomes key
        - Text content becomes value
        - Attributes are stored under '@attributes'
        - Child elements are nested
        """
        result = {}

        # Add attributes if present
        if element.attrib:
            result["@attributes"] = dict(element.attrib)

        # Handle text content
        if element.text and element.text.strip():
            result["@text"] = element.text.strip()

        # Handle child elements
        for child in element:
            child_data = self._xml_to_dict(child)

            # Use child tag as key
            tag = child.tag

            # If key already exists, convert to list
            if tag in result:
                if not isinstance(result[tag], list):
                    result[tag] = [result[tag]]
                result[tag].append(child_data)
            else:
                result[tag] = child_data

        # If element has no children and no attributes, just return text
        if not result and element.text:
            return element.text.strip()

        # If element has only text and no children/attributes, return text
        if list(result.keys()) == ["@text"]:
            return result["@text"]

        return result if result else {}
