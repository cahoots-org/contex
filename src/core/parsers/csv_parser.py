"""CSV/TSV format parser with schema detection"""

import csv
import io
import re
from typing import Any, Optional, Dict, List

from .base import BaseFormatParser, ParseResult


class CSVParser(BaseFormatParser):
    """Parser for CSV and TSV data"""

    @property
    def format_name(self) -> str:
        return "csv"

    @property
    def priority(self) -> int:
        return 11  # Medium priority

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        """Check if data is CSV/TSV"""

        # If hint says it's CSV or TSV, try it
        if format_hint in ["csv", "tsv"]:
            return True

        # Only parse strings
        if not isinstance(data, str):
            return False

        # Reject if it looks like code
        code_patterns = [
            r'^\s*(def|class|import|from|function|const|let|var)\s+',
            r'^\s*#include',
            r'^\s*package\s+',
        ]
        for pattern in code_patterns:
            if re.search(pattern, data, re.MULTILINE):
                return False

        # Reject if it looks like markdown
        if re.search(r'^#{1,6}\s', data, re.MULTILINE):
            return False

        # Reject if it looks like YAML (indented key: value pairs)
        # YAML has consistent indentation with keys followed by colons
        if re.search(r'^\s{2,}\w+:\s', data, re.MULTILINE):
            return False

        # Must have at least 2 lines
        lines = data.strip().split('\n')
        if len(lines) < 2:
            return False

        # Try detecting CSV/TSV by checking for delimiters
        try:
            dialect = csv.Sniffer().sniff(data[:1024])  # Check first KB

            # Verify it actually looks like tabular data
            # Parse a few rows and check for consistent column counts
            reader = csv.reader(io.StringIO(data[:1024]), dialect=dialect)
            rows = list(reader)

            if len(rows) < 2:
                return False

            # Check that most rows have same number of columns
            col_counts = [len(row) for row in rows if row]
            if not col_counts:
                return False

            most_common_count = max(set(col_counts), key=col_counts.count)
            consistent_rows = sum(1 for c in col_counts if c == most_common_count)

            # At least 70% of rows should have same column count
            if consistent_rows / len(col_counts) < 0.7:
                return False

            # Must have at least 2 columns
            if most_common_count < 2:
                return False

            return True
        except:
            return False

    def parse(self, data: Any) -> ParseResult:
        """Parse CSV/TSV data and convert to structured dict"""

        try:
            if not isinstance(data, str):
                return ParseResult(
                    success=False,
                    error="CSV data must be a string"
                )

            # Detect dialect (CSV vs TSV, quote char, etc.)
            try:
                dialect = csv.Sniffer().sniff(data[:1024])
                has_header = csv.Sniffer().has_header(data[:1024])
            except:
                # Fallback to standard CSV
                dialect = 'excel'
                has_header = True

            # Parse CSV
            reader = csv.reader(io.StringIO(data), dialect=dialect)
            rows = list(reader)

            if not rows:
                return ParseResult(
                    success=False,
                    error="CSV is empty"
                )

            # Extract header and data
            if has_header and len(rows) > 1:
                headers = rows[0]
                data_rows = rows[1:]
            else:
                # Generate column names
                headers = [f"col_{i}" for i in range(len(rows[0]))]
                data_rows = rows

            # Convert to list of dicts
            records = []
            for row in data_rows:
                if len(row) == len(headers):
                    record = dict(zip(headers, row))
                    records.append(record)

            # Detect schema (infer types)
            schema = self._detect_schema(records, headers)

            # Convert types in records
            typed_records = self._apply_schema(records, schema)

            normalized_data = {
                "records": typed_records,
                "schema": schema,
                "row_count": len(typed_records),
                "column_count": len(headers)
            }

            return ParseResult(
                success=True,
                normalized_data=normalized_data,
                format_name=self.format_name,
                is_structured=True,
                metadata={
                    "dialect": str(dialect),
                    "has_header": has_header,
                    "columns": headers
                }
            )

        except Exception as e:
            return ParseResult(
                success=False,
                error=f"CSV parse error: {str(e)}"
            )

    def _detect_schema(
        self,
        records: List[Dict[str, str]],
        headers: List[str]
    ) -> Dict[str, str]:
        """
        Detect column types from data.

        Returns dict mapping column name -> type ("int", "float", "bool", "string")
        """
        schema = {}

        for header in headers:
            # Sample values from this column
            values = [r.get(header, "") for r in records[:100]]  # Sample first 100 rows

            # Try detecting type
            col_type = self._infer_type(values)
            schema[header] = col_type

        return schema

    def _infer_type(self, values: List[str]) -> str:
        """Infer type from list of string values"""

        non_empty = [v.strip() for v in values if v and v.strip()]
        if not non_empty:
            return "string"

        # Try integer
        try:
            for v in non_empty:
                int(v)
            return "int"
        except:
            pass

        # Try float
        try:
            for v in non_empty:
                float(v)
            return "float"
        except:
            pass

        # Try boolean
        bool_values = {"true", "false", "yes", "no", "1", "0", "t", "f", "y", "n"}
        if all(v.lower() in bool_values for v in non_empty):
            return "bool"

        # Default to string
        return "string"

    def _apply_schema(
        self,
        records: List[Dict[str, str]],
        schema: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """Convert string values to typed values based on schema"""

        typed_records = []

        for record in records:
            typed_record = {}
            for key, value in record.items():
                col_type = schema.get(key, "string")
                typed_record[key] = self._convert_value(value, col_type)
            typed_records.append(typed_record)

        return typed_records

    def _convert_value(self, value: str, col_type: str) -> Any:
        """Convert string value to typed value"""

        if not value or not value.strip():
            return None

        try:
            if col_type == "int":
                return int(value)
            elif col_type == "float":
                return float(value)
            elif col_type == "bool":
                return value.lower() in {"true", "yes", "1", "t", "y"}
            else:
                return value
        except:
            return value  # Fallback to string
