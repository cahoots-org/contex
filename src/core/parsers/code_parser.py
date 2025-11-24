"""Code parser for extracting structure from source code"""

import re
from typing import Any, Optional, Dict, List

from .base import BaseFormatParser, ParseResult


class CodeParser(BaseFormatParser):
    """Parser for source code (Python, JavaScript, etc.)"""

    @property
    def format_name(self) -> str:
        return "code"

    @property
    def priority(self) -> int:
        return 21  # Low priority

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        """Check if data is source code"""

        # If hint says it's code/python/javascript, try it
        if format_hint in ["code", "python", "py", "javascript", "js", "typescript", "ts"]:
            return True

        # Only parse strings
        if not isinstance(data, str):
            return False

        # Look for code patterns
        code_patterns = [
            r'^\s*(def|class|function|const|let|var|import|from)\s',  # Definitions/imports
            r'^\s*@\w+',  # Decorators
            r'=>\s*{',  # Arrow functions
            r'^\s*(public|private|protected)\s',  # Access modifiers
        ]

        for pattern in code_patterns:
            if re.search(pattern, data, re.MULTILINE):
                return True

        return False

    def parse(self, data: Any) -> ParseResult:
        """Parse source code and extract structure"""

        try:
            if not isinstance(data, str):
                return ParseResult(
                    success=False,
                    error="Code data must be a string"
                )

            # Detect language
            language = self._detect_language(data)

            # Extract structure based on language
            if language == "python":
                structure = self._extract_python_structure(data)
            elif language in ["javascript", "typescript"]:
                structure = self._extract_js_structure(data)
            else:
                structure = self._extract_generic_structure(data)

            # Create normalized representation
            normalized_data = {
                "content": data,
                "content_type": "code",
                "language": language,
                "structure": structure
            }

            return ParseResult(
                success=True,
                normalized_data=normalized_data,
                format_name=self.format_name,
                is_structured=False,  # Consider code unstructured
                metadata={"language": language, **structure}
            )

        except Exception as e:
            return ParseResult(
                success=False,
                error=f"Code parse error: {str(e)}"
            )

    def _detect_language(self, code: str) -> str:
        """Detect programming language"""

        # Python indicators
        python_keywords = ["def ", "class ", "import ", "from ", "elif ", "pass"]
        python_score = sum(1 for kw in python_keywords if kw in code)

        # JavaScript/TypeScript indicators
        js_keywords = ["function ", "const ", "let ", "var ", "=>", "interface ", "type "]
        js_score = sum(1 for kw in js_keywords if kw in code)

        if python_score > js_score:
            return "python"
        elif js_score > 0:
            return "typescript" if "interface " in code or ": " in code else "javascript"
        else:
            return "unknown"

    def _extract_python_structure(self, code: str) -> Dict[str, Any]:
        """Extract Python-specific structure"""

        structure = {
            "functions": self._extract_python_functions(code),
            "classes": self._extract_python_classes(code),
            "imports": self._extract_python_imports(code),
            "decorators": self._extract_python_decorators(code)
        }

        return structure

    def _extract_python_functions(self, code: str) -> List[Dict[str, str]]:
        """Extract Python function definitions"""

        functions = []
        func_pattern = r'^\s*def\s+(\w+)\s*\((.*?)\)(?:\s*->\s*([^:]+))?:'

        for match in re.finditer(func_pattern, code, re.MULTILINE):
            functions.append({
                "name": match.group(1),
                "params": match.group(2).strip(),
                "return_type": match.group(3).strip() if match.group(3) else None
            })

        return functions

    def _extract_python_classes(self, code: str) -> List[Dict[str, Any]]:
        """Extract Python class definitions"""

        classes = []
        class_pattern = r'^\s*class\s+(\w+)(?:\(([^)]*)\))?:'

        for match in re.finditer(class_pattern, code, re.MULTILINE):
            classes.append({
                "name": match.group(1),
                "bases": match.group(2).strip() if match.group(2) else None
            })

        return classes

    def _extract_python_imports(self, code: str) -> List[str]:
        """Extract Python imports"""

        imports = []

        # import statements
        for match in re.finditer(r'^\s*import\s+(.+)$', code, re.MULTILINE):
            imports.extend([imp.strip() for imp in match.group(1).split(',')])

        # from...import statements
        for match in re.finditer(r'^\s*from\s+(\S+)\s+import', code, re.MULTILINE):
            imports.append(match.group(1))

        return list(set(imports))  # Deduplicate

    def _extract_python_decorators(self, code: str) -> List[str]:
        """Extract Python decorators"""

        decorators = []
        decorator_pattern = r'^\s*@(\w+(?:\.\w+)*)'

        for match in re.finditer(decorator_pattern, code, re.MULTILINE):
            decorators.append(match.group(1))

        return list(set(decorators))

    def _extract_js_structure(self, code: str) -> Dict[str, Any]:
        """Extract JavaScript/TypeScript structure"""

        structure = {
            "functions": self._extract_js_functions(code),
            "classes": self._extract_js_classes(code),
            "imports": self._extract_js_imports(code),
            "exports": self._extract_js_exports(code)
        }

        return structure

    def _extract_js_functions(self, code: str) -> List[Dict[str, str]]:
        """Extract JS function definitions"""

        functions = []

        # Regular functions
        for match in re.finditer(r'function\s+(\w+)\s*\((.*?)\)', code):
            functions.append({"name": match.group(1), "type": "function"})

        # Arrow functions
        for match in re.finditer(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:\([^)]*\)|[^=])*\s*=>', code):
            functions.append({"name": match.group(1), "type": "arrow"})

        return functions

    def _extract_js_classes(self, code: str) -> List[Dict[str, str]]:
        """Extract JS class definitions"""

        classes = []
        class_pattern = r'class\s+(\w+)(?:\s+extends\s+(\w+))?'

        for match in re.finditer(class_pattern, code):
            classes.append({
                "name": match.group(1),
                "extends": match.group(2) if match.group(2) else None
            })

        return classes

    def _extract_js_imports(self, code: str) -> List[str]:
        """Extract JS imports"""

        imports = []

        # ES6 imports
        for match in re.finditer(r'import\s+.*?from\s+["\']([^"\']+)["\']', code):
            imports.append(match.group(1))

        # require()
        for match in re.finditer(r'require\(["\']([^"\']+)["\']\)', code):
            imports.append(match.group(1))

        return list(set(imports))

    def _extract_js_exports(self, code: str) -> List[str]:
        """Extract JS exports"""

        exports = []

        # export statements
        for match in re.finditer(r'export\s+(?:const|let|var|function|class)\s+(\w+)', code):
            exports.append(match.group(1))

        return exports

    def _extract_generic_structure(self, code: str) -> Dict[str, Any]:
        """Extract generic code structure"""

        # Basic stats for unknown languages
        lines = code.split('\n')

        return {
            "line_count": len(lines),
            "non_empty_lines": len([l for l in lines if l.strip()]),
            "comment_lines": len([l for l in lines if l.strip().startswith(('#', '//', '/*', '*'))]),
        }
