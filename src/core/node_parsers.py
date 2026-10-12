"""
Format parsers that convert various data formats into Nodes.

Each parser detects whether it can handle the input and parses it into Nodes.
"""

from typing import Any, List, Optional
from abc import ABC, abstractmethod
import csv
import json
import os
import re
import io
import subprocess
import zipfile

import pypdfium2
import yaml
from docx import Document
from PIL import Image
from tree_sitter_language_pack import get_parser

from .node import Node, NodeType, ParseResult


# Extensions we can parse as code, mapped to their tree-sitter grammar name.
# Single source of truth: resolve_format only returns "code" for languages the
# CodeNodeParser actually handles, so detection and parsing never disagree.
CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
}

# Other formats a file extension identifies.
_EXTENSION_FORMATS = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".csv": "csv",
    ".pdf": "pdf",
    ".docx": "docx",
    **dict.fromkeys((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"), "image"),
}

# data_format values that carry no real signal, so the key's extension or
# content sniffing decides the format.
GENERIC_FORMATS = {None, "", "text"}

# OCR scanned PDF pages and images with Tesseract (must be installed).
OCR_ENABLED = os.getenv("OCR_ENABLED", "").strip().lower() in ("1", "true", "yes")

# Bounds on what one uploaded document may cost to parse.
MAX_PDF_PAGES = 2000
MAX_OCR_PAGES = 50
MAX_OCR_PIXELS_PER_SIDE = 6000
OCR_TIMEOUT_SECONDS = 120
MAX_DOCX_UNZIPPED_BYTES = 200 * 2**20
MAX_NODES = 10_000


class DocumentRejected(ValueError):
    """A document this server won't ingest; its message is safe to show the publisher."""


def _add(nodes: List[Node], node: Node) -> None:
    """Append ``node``, refusing a document once it exceeds MAX_NODES parts."""
    if len(nodes) >= MAX_NODES:
        raise DocumentRejected(f"document has more than {MAX_NODES} parts; split it into smaller documents")
    nodes.append(node)


def _extension(data_key: Optional[str]) -> str:
    """Lowercased suffix of a file-ish key ("owner/repo:src/app.py" -> ".py"), or ""."""
    base = (data_key or "").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    dot = base.rfind(".")
    return base[dot:].lower() if dot != -1 else ""


def language_for_key(data_key: Optional[str]) -> Optional[str]:
    """Grammar name for a data_key's file extension, or None."""
    return CODE_EXTENSIONS.get(_extension(data_key))


def resolve_format(data_format: Optional[str], data_key: Optional[str]) -> Optional[str]:
    """Resolve the effective format for a publish, inferring it from the key.

    An explicit, non-generic ``data_format`` always wins. A generic/absent one
    ("text" or None, as emitted by the GitHub/S3 connectors) falls back to the
    key's extension, or is returned unchanged when the extension says nothing.
    """
    if data_format not in GENERIC_FORMATS:
        return data_format
    if language_for_key(data_key):
        return "code"
    return _EXTENSION_FORMATS.get(_extension(data_key), data_format)


class BaseNodeParser(ABC):
    """Base class for all node parsers"""

    # Parsers that need the source key (e.g. to pick a language from the file
    # extension) set this; the converter then passes data_key into parse().
    needs_data_key: bool = False

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Name of the format (e.g., 'json', 'markdown')"""
        pass

    @property
    @abstractmethod
    def priority(self) -> int:
        """Priority (lower = try first)"""
        pass

    @abstractmethod
    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        """Check if this parser can handle the data"""
        pass

    @abstractmethod
    def parse(self, data: Any) -> ParseResult:
        """Parse data into nodes"""
        pass


# ============================================================================
# JSON Parser
# ============================================================================

def _is_complex(value: Any) -> bool:
    """True for a nested object or an array of objects."""
    return isinstance(value, dict) or (isinstance(value, list) and bool(value) and isinstance(value[0], dict))


class JSONNodeParser(BaseNodeParser):
    """Parse JSON/dict data into nodes"""

    @property
    def format_name(self) -> str:
        return "json"

    @property
    def priority(self) -> int:
        return 10

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        if format_hint == "json":
            return True
        if isinstance(data, (dict, list)):
            return True
        if isinstance(data, str):
            try:
                json.loads(data)
                return True
            except (json.JSONDecodeError, TypeError):
                return False
        return False

    def parse(self, data: Any) -> ParseResult:
        """Parse JSON into nodes"""
        # Parse string to dict if needed
        if isinstance(data, str):
            data = json.loads(data)

        nodes = self._extract_nodes(data, "", [])
        return ParseResult(
            nodes=nodes,
            format_name="json",
            success=True,
            metadata={"node_count": len(nodes)}
        )

    def _extract_nodes(self, data: Any, path: str, nodes: List[Node]) -> List[Node]:
        """Recursively append the JSON structure's nodes to ``nodes`` and return it."""
        if isinstance(data, dict):
            # Primitive fields (and lists of primitives) stay with this object;
            # nested objects and arrays of objects become their own nodes.
            own = {k: v for k, v in data.items() if not _is_complex(v)}
            # An empty dict still gets a node so it isn't silently dropped. The
            # top-level object's path is "", so its node_key is the data_key.
            if own or not data:
                _add(nodes, Node(
                    path=path,
                    content=own,
                    node_type=NodeType.OBJECT,
                    metadata={"format": "json"}
                ))
            for key, value in data.items():
                # Escaped so {"a.b": ...} and {"a": {"b": ...}} get distinct paths.
                segment = str(key).replace("\\", "\\\\").replace(".", "\\.").replace("[", "\\[")
                child_path = f"{path}.{segment}" if path else segment
                if isinstance(value, list) and _is_complex(value):
                    for idx, item in enumerate(value):
                        self._extract_nodes(item, f"{child_path}[{idx}]", nodes)
                elif isinstance(value, dict):
                    self._extract_nodes(value, child_path, nodes)

        elif isinstance(data, list):
            # Top-level array
            for idx, item in enumerate(data):
                item_path = f"{path}[{idx}]" if path else f"[{idx}]"
                self._extract_nodes(item, item_path, nodes)

        else:
            # Primitive value
            _add(nodes, Node(
                path=path or "value",
                content=data,
                node_type=NodeType.PRIMITIVE,
                metadata={"format": "json"}
            ))

        return nodes


# ============================================================================
# YAML Parser
# ============================================================================

class _NoAliasLoader(yaml.SafeLoader):
    """Safe loader that rejects aliases, which can expand a few bytes into millions of nodes."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML aliases are not supported")
        return super().compose_node(parent, index)


class YAMLNodeParser(JSONNodeParser):
    """Parse YAML data (reuses JSON logic since YAML → dict)"""

    @property
    def format_name(self) -> str:
        return "yaml"

    @property
    def priority(self) -> int:
        return 11

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        # Too much prose ("Owner: x") is valid YAML to sniff for it.
        return format_hint == "yaml"

    def parse(self, data: Any) -> ParseResult:
        """Parse YAML into nodes"""
        if isinstance(data, str):
            data = yaml.load(data, Loader=_NoAliasLoader)

        nodes = self._extract_nodes(data, "", [])
        return ParseResult(
            nodes=nodes,
            format_name="yaml",
            success=True,
            metadata={"node_count": len(nodes)}
        )


# ============================================================================
# Plain Text Parser
# ============================================================================

class PlainTextNodeParser(BaseNodeParser):
    """Parse plain text into sentence/paragraph nodes"""

    @property
    def format_name(self) -> str:
        return "text"

    @property
    def priority(self) -> int:
        return 100  # Fallback parser

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        return isinstance(data, str)

    def parse(self, data: Any) -> ParseResult:
        """Parse text into nodes"""
        text = str(data).strip()

        if not text:
            return ParseResult(
                nodes=[],
                format_name="text",
                success=True
            )

        nodes = []

        # Split into paragraphs
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

        if len(paragraphs) == 1:
            # Single paragraph - split into sentences
            sentences = self._split_sentences(paragraphs[0])
            for i, sentence in enumerate(sentences):
                _add(nodes, Node(
                    path=f"sentence_{i}",
                    content=sentence,
                    node_type=NodeType.SENTENCE,
                    metadata={"format": "text"}
                ))
        else:
            # Multiple paragraphs
            for i, para in enumerate(paragraphs):
                _add(nodes, Node(
                    path=f"paragraph_{i}",
                    content=para,
                    node_type=NodeType.PARAGRAPH,
                    metadata={"format": "text"}
                ))

        return ParseResult(
            nodes=nodes,
            format_name="text",
            success=True,
            metadata={"node_count": len(nodes)}
        )

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences"""
        # Simple sentence splitting
        sentences = re.split(r'[.!?]+\s+', text)
        return [s.strip() for s in sentences if s.strip()]


# ============================================================================
# Markdown Parser
# ============================================================================

class MarkdownNodeParser(BaseNodeParser):
    """Parse Markdown into structured nodes"""

    @property
    def format_name(self) -> str:
        return "markdown"

    @property
    def priority(self) -> int:
        return 20

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        if format_hint == "markdown":
            return True
        if isinstance(data, str):
            # Check for markdown patterns
            return bool(re.search(r'^#{1,6}\s', data, re.MULTILINE) or
                       re.search(r'```', data) or
                       re.search(r'^\*|\d+\.', data, re.MULTILINE))
        return False

    def parse(self, data: Any) -> ParseResult:
        """Parse markdown into nodes"""
        text = str(data).strip()
        nodes = []

        # Split by headings
        sections = re.split(r'^(#{1,6}\s+.+)$', text, flags=re.MULTILINE)

        current_heading = None
        section_idx = 0

        for i, part in enumerate(sections):
            part = part.strip()
            if not part:
                continue

            # Check if it's a heading
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', part)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2)
                current_heading = title
                _add(nodes, Node(
                    path=f"heading_{section_idx}",
                    content=title,
                    node_type=NodeType.HEADING,
                    metadata={"format": "markdown", "level": level}
                ))
                section_idx += 1
            else:
                # Content section
                # Check for code blocks
                code_blocks = list(re.finditer(r'```(\w*)\n(.*?)```', part, re.DOTALL))

                if code_blocks:
                    for cb in code_blocks:
                        language = cb.group(1) or "text"
                        code = cb.group(2).strip()
                        _add(nodes, Node(
                            path=f"code_block_{len(nodes)}",
                            content=code,
                            node_type=NodeType.CODE_BLOCK,
                            metadata={"format": "markdown", "language": language}
                        ))

                # Remove code blocks and parse remaining as paragraphs
                remaining = re.sub(r'```.*?```', '', part, flags=re.DOTALL).strip()
                if remaining:
                    paragraphs = [p.strip() for p in remaining.split('\n\n') if p.strip()]
                    for para in paragraphs:
                        _add(nodes, Node(
                            path=f"paragraph_{len(nodes)}",
                            content=para,
                            node_type=NodeType.PARAGRAPH,
                            metadata={"format": "markdown", "heading": current_heading}
                        ))

        return ParseResult(
            nodes=nodes,
            format_name="markdown",
            success=True,
            metadata={"node_count": len(nodes)}
        )


# ============================================================================
# CSV Parser
# ============================================================================

class CSVNodeParser(BaseNodeParser):
    """Parse CSV data into row nodes"""

    @property
    def format_name(self) -> str:
        return "csv"

    @property
    def priority(self) -> int:
        return 15

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        if format_hint == "csv":
            return True
        return isinstance(data, str) and self._looks_like_csv(data)

    @staticmethod
    def _looks_like_csv(text: str) -> bool:
        """True when one delimiter has a consistent per-line count: real rows,
        not just commas (which code is full of, in varying counts)."""
        lines = [ln for ln in text.strip().splitlines() if ln.strip()]
        if len(lines) < 2:
            return False
        sample = lines[:50]
        for delim in (",", "\t", ";"):
            counts = [ln.count(delim) for ln in sample]
            modal = max(set(counts), key=counts.count)
            if modal >= 1 and counts.count(modal) >= 0.9 * len(sample):
                return True
        return False

    def parse(self, data: Any) -> ParseResult:
        """Parse CSV into row nodes"""
        text = str(data).strip()
        reader = csv.DictReader(io.StringIO(text))

        nodes = []
        for i, row in enumerate(reader):
            _add(nodes, Node(
                path=f"row_{i}",
                content=dict(row),
                node_type=NodeType.ROW,
                metadata={"format": "csv"}
            ))

        return ParseResult(
            nodes=nodes,
            format_name="csv",
            success=True,
            metadata={"node_count": len(nodes)}
        )


# ============================================================================
# PDF Parser
# ============================================================================

def _ocr(image: bytes) -> str:
    """The text Tesseract reads from an image file's bytes."""
    result = subprocess.run(
        ["tesseract", "stdin", "stdout"], input=image, capture_output=True, check=True, timeout=OCR_TIMEOUT_SECONDS,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _ocr_page(page: pypdfium2.PdfPage) -> str:
    """OCR a page rendered at 300 dpi, or smaller if that would be huge."""
    scale = min(300 / 72, MAX_OCR_PIXELS_PER_SIDE / max(page.get_size()))
    pixels = page.render(scale=scale, grayscale=True).to_numpy()
    height, width = pixels.shape
    return _ocr(b"P5 %d %d 255\n" % (width, height) + pixels.tobytes())


class PDFNodeParser(BaseNodeParser):
    """Parse PDF documents into nodes by extracting text per page/section.

    Up to MAX_OCR_PAGES pages with no text layer (scans) are OCR'd when
    OCR_ENABLED is set.
    """

    @property
    def format_name(self) -> str:
        return "pdf"

    @property
    def priority(self) -> int:
        return 5  # High priority when format hint matches

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        if format_hint == "pdf":
            return True
        # Check for PDF magic bytes
        if isinstance(data, (bytes, bytearray)):
            return data[:5] == b'%PDF-'
        return False

    def _page_texts(self, data: bytes) -> List[str]:
        pdf = pypdfium2.PdfDocument(data)
        try:
            if len(pdf) > MAX_PDF_PAGES:
                raise DocumentRejected(f"PDF has more than {MAX_PDF_PAGES} pages")
            texts = [page.get_textpage().get_text_bounded().replace("\r\n", "\n").strip() for page in pdf]
            if OCR_ENABLED:
                scanned = [i for i, text in enumerate(texts) if not text]
                for i in scanned[:MAX_OCR_PAGES]:
                    texts[i] = _ocr_page(pdf[i])
            return texts
        finally:
            pdf.close()

    def parse(self, data: Any) -> ParseResult:
        """Parse document bytes into nodes"""
        fmt = self.format_name
        if not isinstance(data, (bytes, bytearray)):
            return ParseResult(nodes=[], format_name=fmt, success=False, error=f"{fmt} parser requires bytes input")
        texts = self._page_texts(data)
        nodes = []
        total_pages = len(texts)

        for page_num, text in enumerate(texts):
            if not text:
                continue

            # Split page text into paragraphs
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

            if len(paragraphs) <= 1:
                # Single block of text for this page — one section node
                _add(nodes, Node(
                    path=f"page_{page_num + 1}",
                    content=text,
                    node_type=NodeType.SECTION,
                    metadata={
                        "format": fmt,
                        "page": page_num + 1,
                        "total_pages": total_pages,
                    }
                ))
            else:
                # Multiple paragraphs — create individual nodes
                for i, para in enumerate(paragraphs):
                    _add(nodes, Node(
                        path=f"page_{page_num + 1}.paragraph_{i}",
                        content=para,
                        node_type=NodeType.PARAGRAPH,
                        metadata={
                            "format": fmt,
                            "page": page_num + 1,
                            "total_pages": total_pages,
                        }
                    ))

        if not nodes:
            raise DocumentRejected(f"no text found in the {fmt}")
        return ParseResult(
            nodes=nodes,
            format_name=fmt,
            success=True,
            metadata={
                "node_count": len(nodes),
                "total_pages": total_pages,
            }
        )


class ImageNodeParser(PDFNodeParser):
    """OCR an image (PNG, JPEG, TIFF, ...) into nodes; requires OCR_ENABLED."""

    @property
    def format_name(self) -> str:
        return "image"

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        return format_hint == "image"

    def _page_texts(self, data: bytes) -> List[str]:
        if not OCR_ENABLED:
            raise DocumentRejected("reading text from images is not enabled on this server")
        with Image.open(io.BytesIO(data)) as image:
            if max(image.size) > MAX_OCR_PIXELS_PER_SIDE or getattr(image, "n_frames", 1) > 1:
                raise DocumentRejected(f"image must be one frame of at most {MAX_OCR_PIXELS_PER_SIDE} pixels a side")
        return [_ocr(data)]


# ============================================================================
# DOCX Parser
# ============================================================================

class DOCXNodeParser(BaseNodeParser):
    """Parse DOCX documents into nodes using python-docx"""

    @property
    def format_name(self) -> str:
        return "docx"

    @property
    def priority(self) -> int:
        return 5  # High priority when format hint matches

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        if format_hint == "docx":
            return True
        # Check for ZIP/DOCX magic bytes (PK header)
        if isinstance(data, (bytes, bytearray)):
            return data[:2] == b'PK' and b'word/' in data[:2000]
        return False

    def parse(self, data: Any) -> ParseResult:
        """Parse DOCX bytes into nodes"""
        if isinstance(data, (bytes, bytearray)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                unzipped = sum(info.file_size for info in archive.infolist())
            if unzipped > MAX_DOCX_UNZIPPED_BYTES:
                raise DocumentRejected("DOCX expands beyond the size limit")
            doc = Document(io.BytesIO(data))
        else:
            return ParseResult(
                nodes=[],
                format_name="docx",
                success=False,
                error="DOCX parser requires bytes input"
            )

        # Paragraph styles are not read: python-docx resolves each one by scanning every style.
        nodes = []
        texts = [p.text.strip() for p in doc.paragraphs]
        for i, text in enumerate(t for t in texts if t):
            _add(nodes, Node(path=f"paragraph_{i}", content=text, node_type=NodeType.PARAGRAPH,
                             metadata={"format": "docx"}))

        # Extract tables
        for t_idx, table in enumerate(doc.tables):
            # Get headers from first row
            headers = []
            if table.rows:
                headers = [cell.text.strip() for cell in table.rows[0].cells]

            for r_idx, row in enumerate(table.rows[1:], start=1):
                row_data = {}
                for c_idx, cell in enumerate(row.cells):
                    key = headers[c_idx] if c_idx < len(headers) else f"col_{c_idx}"
                    row_data[key] = cell.text.strip()
                if any(v for v in row_data.values()):
                    _add(nodes, Node(
                        path=f"table_{t_idx}.row_{r_idx}",
                        content=row_data,
                        node_type=NodeType.ROW,
                        metadata={
                            "format": "docx",
                            "table_index": t_idx,
                        }
                    ))

        return ParseResult(
            nodes=nodes,
            format_name="docx",
            success=True,
            metadata={"node_count": len(nodes)}
        )


# ============================================================================
# Code Parser (tree-sitter)
# ============================================================================

_KIND_TO_NODETYPE = {
    "function": NodeType.FUNCTION,
    "class": NodeType.CLASS,
    "method": NodeType.METHOD,
}

# Per-language AST shape. We walk the tree-sitter tree directly rather than using
# tags.scm queries — the language pack ships grammars but no query files, and the
# node-type names below are stable across grammar versions. "class" covers any
# type-defining construct (Python/TS class, Go type) so it maps to NodeType.CLASS.
_JS_CONFIG = {
    "defs": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "call_type": "call_expression",
    "call_field": "function",
    "member_type": "member_expression",
    "member_field": "property",
    "import_types": {"import_statement"},
    "import_lang": "js",
    "var_fn": True,  # const x = () => {} / function expression assigned to a name
}

_LANGUAGE_CONFIG = {
    "python": {
        "defs": {"function_definition": "function", "class_definition": "class"},
        "call_type": "call",
        "call_field": "function",
        "member_type": "attribute",
        "member_field": "attribute",
        "import_types": {"import_from_statement"},
        "import_lang": "python",
        "var_fn": False,
    },
    "javascript": _JS_CONFIG,
    "typescript": _JS_CONFIG,
    "tsx": _JS_CONFIG,
    "go": {
        "defs": {
            "function_declaration": "function",
            "method_declaration": "method",
            "type_spec": "class",
        },
        "call_type": "call_expression",
        "call_field": "function",
        "member_type": "selector_expression",
        "member_field": "field",
        "import_types": set(),  # Go imports are package names, not symbol defs.
        "import_lang": None,
        "var_fn": False,
    },
}


def _own_text(src: bytes, start: int, end: int, holes: List[dict]) -> str:
    """``src[start:end]`` with the byte spans of the (ordered) ``holes`` cut out."""
    parts, pos = [], start
    for hole in holes:
        parts.append(src[pos:hole["start_byte"]])
        pos = hole["end_byte"]
    parts.append(src[pos:end])
    return b"".join(parts).decode("utf-8", errors="replace")


def _chunk_blocks(text: str, limit: int = 1500) -> List[str]:
    """Blank-line-separated blocks of ``text``, merged greedily up to ``limit`` characters."""
    chunks: List[str] = []
    for block in (b.strip() for b in text.split("\n\n")):
        if not block:
            continue
        if chunks and len(chunks[-1]) + len(block) < limit:
            chunks[-1] += "\n\n" + block
        else:
            chunks.append(block)
    return chunks


class CodeNodeParser(BaseNodeParser):
    """Parse source code into function/class/method nodes via tree-sitter.

    Emits one node per definition (with dotted node_path for nesting) plus a
    file-level node (path "") carrying module imports. Each node records, in its
    metadata, the identifiers it defines (``defs``) and references (``refs``:
    imports + call targets) — the input to the symbols table and cross-file link
    join. The language is chosen from the data_key's file extension.
    """

    needs_data_key = True

    @property
    def format_name(self) -> str:
        return "code"

    @property
    def priority(self) -> int:
        return 5

    def can_parse(self, data: Any, format_hint: Optional[str] = None) -> bool:
        return format_hint == "code"

    def parse(self, data: Any, data_key: Optional[str] = None) -> ParseResult:
        lang = language_for_key(data_key)
        cfg = _LANGUAGE_CONFIG.get(lang) if lang else None
        if cfg is None:
            return ParseResult(
                nodes=[], format_name="code", success=False,
                error=f"no supported grammar for key {data_key!r}",
            )
        src = bytes(data) if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")
        tree = get_parser(lang).parse(src)
        nodes = self._walk(tree.root_node, src, lang, cfg)
        return ParseResult(
            nodes=nodes, format_name="code", success=True,
            metadata={"language": lang, "node_count": len(nodes)},
        )

    @staticmethod
    def _txt(node, src: bytes) -> str:
        return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _name_of(self, node, src: bytes) -> Optional[str]:
        n = node.child_by_field_name("name")
        return self._txt(n, src) if n is not None else None

    def _callee_name(self, call_node, cfg, src: bytes) -> Optional[str]:
        fn = call_node.child_by_field_name(cfg["call_field"])
        if fn is None:
            return None
        if fn.type == cfg["member_type"]:
            prop = fn.child_by_field_name(cfg["member_field"])
            return self._txt(prop, src) if prop is not None else None
        if fn.type == "identifier":
            return self._txt(fn, src)
        return None

    def _import_names(self, node, cfg, src: bytes) -> List[str]:
        names: List[str] = []
        if cfg["import_lang"] == "python":
            # from pkg.mod import a, b as c  -> [a, b]; skip the module itself.
            module = node.child_by_field_name("module_name")
            for child in node.named_children:
                if child == module:
                    continue
                if child.type == "dotted_name":
                    names.append(self._txt(child, src).split(".")[-1])
                elif child.type == "aliased_import":
                    nm = child.child_by_field_name("name")
                    if nm is not None:
                        names.append(self._txt(nm, src).split(".")[-1])
                elif child.type == "identifier":
                    names.append(self._txt(child, src))
        elif cfg["import_lang"] == "js":
            # import { a, b } from "m"; import def from "m"  -> [a, b, def]
            stack = [node]
            while stack:
                n = stack.pop()
                if n.type == "import_specifier":
                    nm = n.child_by_field_name("name")
                    if nm is not None:
                        names.append(self._txt(nm, src))
                elif n.type == "identifier" and n.parent is not None and n.parent.type == "import_clause":
                    names.append(self._txt(n, src))
                stack.extend(n.children)
        return names

    def _walk(self, root, src: bytes, lang: str, cfg) -> List[Node]:
        records: List[dict] = []
        module = {"refs": [], "holes": []}

        def add(seq: List[str], name: Optional[str]) -> None:
            if name and name not in seq:
                seq.append(name)

        def visit(node, scope: dict, stack) -> None:
            t = node.type
            # Definition -> its own node; descend with it as the enclosing scope.
            if t in cfg["defs"]:
                kind = cfg["defs"][t]
                if kind == "function" and any(k == "class" for k, _ in stack):
                    kind = "method"
                name = self._name_of(node, src)
                if name:
                    rec = self._record(node, kind, name, stack)
                    records.append(rec)
                    scope["holes"].append(rec)
                    for c in node.children:
                        visit(c, rec, stack + [(kind, name)])
                    return
            # const x = () => {} / function expression assigned to a name.
            if cfg["var_fn"] and t == "variable_declarator":
                value = node.child_by_field_name("value")
                if value is not None and value.type in (
                    "arrow_function", "function", "function_expression",
                ):
                    name = self._name_of(node, src)
                    if name:
                        kind = "method" if any(k == "class" for k, _ in stack) else "function"
                        rec = self._record(node, kind, name, stack)
                        records.append(rec)
                        scope["holes"].append(rec)
                        for c in node.children:
                            visit(c, rec, stack + [(kind, name)])
                        return
            if t == cfg["call_type"]:
                add(scope["refs"], self._callee_name(node, cfg, src))
            if t in cfg["import_types"]:
                for nm in self._import_names(node, cfg, src):
                    add(module["refs"], nm)
            for c in node.children:
                visit(c, scope, stack)

        visit(root, module, [])

        # Same-named defs in one scope (property getter/setter, overloads) would
        # share a path, and node_key must be unique; suffix repeats with #n.
        seen: dict = {}
        for r in records:
            seen[r["path"]] = seen.get(r["path"], 0) + 1
            if seen[r["path"]] > 1:
                r["path"] = f'{r["path"]}#{seen[r["path"]]}'

        summary = "defines: " + ", ".join(r["path"] for r in records)
        if module["refs"]:
            summary += "\nreferences: " + ", ".join(module["refs"])
        nodes: List[Node] = []
        _add(nodes, Node(
            path="", content=summary, node_type=NodeType.DOCUMENT,
            metadata={"language": lang, "defs": [], "refs": module["refs"]},
        ))
        # Module-level code (constants, route registrations, scripts) is
        # everything outside the top-level definitions.
        for i, chunk in enumerate(_chunk_blocks(_own_text(src, 0, len(src), module["holes"]))):
            _add(nodes, Node(
                path=f"<module>[{i}]", content=chunk, node_type=NodeType.CODE_BLOCK,
                metadata={"language": lang, "defs": [], "refs": []},
            ))
        for r in records:
            _add(nodes, Node(
                path=r["path"], content=_own_text(src, r["start_byte"], r["end_byte"], r["holes"]),
                node_type=_KIND_TO_NODETYPE[r["kind"]],
                metadata={
                    "language": lang, "defs": r["defs"], "refs": r["refs"],
                    "start_line": r["start"], "end_line": r["end"],
                },
            ))
        return nodes

    def _record(self, node, kind: str, name: str, stack) -> dict:
        return {
            "kind": kind, "name": name,
            "path": ".".join([n for _, n in stack] + [name]),
            "start": node.start_point[0] + 1, "end": node.end_point[0] + 1,
            "start_byte": node.start_byte, "end_byte": node.end_byte,
            "defs": [name], "refs": [], "holes": [],
        }
