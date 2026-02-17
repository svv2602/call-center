"""Document parsers for knowledge base bulk import.

Supports Markdown, PDF, and DOCX file formats.
Extracts title and plain text content from uploaded files.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from src.knowledge.categories import CATEGORY_VALUES

logger = logging.getLogger(__name__)


def parse_markdown(content: bytes, filename: str) -> tuple[str, str]:
    """Parse a Markdown file, extracting H1 as title.

    Args:
        content: Raw file bytes.
        filename: Original filename.

    Returns:
        Tuple of (title, body_text).
    """
    text = content.decode("utf-8", errors="replace")
    lines = text.strip().split("\n")

    title = Path(filename).stem.replace("-", " ").replace("_", " ").title()
    body_lines: list[str] = []
    title_found = False

    for line in lines:
        if not title_found and line.startswith("# ") and not line.startswith("##"):
            title = line[2:].strip()
            title_found = True
        else:
            body_lines.append(line)

    body = "\n".join(body_lines).strip()
    return title, body


def parse_pdf(content: bytes, filename: str) -> tuple[str, str]:
    """Parse a PDF file, extracting text from all pages.

    Args:
        content: Raw file bytes.
        filename: Original filename.

    Returns:
        Tuple of (title, body_text).
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages_text: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            pages_text.append(page_text)

    title = Path(filename).stem.replace("-", " ").replace("_", " ").title()
    body = "\n\n".join(pages_text).strip()
    return title, body


def parse_docx(content: bytes, filename: str) -> tuple[str, str]:
    """Parse a DOCX file, extracting headings and paragraphs.

    Args:
        content: Raw file bytes.
        filename: Original filename.

    Returns:
        Tuple of (title, body_text).
    """
    from docx import Document

    doc = Document(io.BytesIO(content))

    title = Path(filename).stem.replace("-", " ").replace("_", " ").title()
    paragraphs: list[str] = []
    title_found = False

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if not title_found and para.style.name.startswith("Heading"):
            title = text
            title_found = True
        else:
            paragraphs.append(text)

    body = "\n\n".join(paragraphs).strip()
    return title, body


def detect_category_from_filename(filename: str) -> str:
    """Detect article category from filename.

    Looks for category keywords in the filename.
    Falls back to 'general' if no match found.

    Args:
        filename: Original filename.

    Returns:
        Matching category value.
    """
    name_lower = filename.lower()
    for cat in CATEGORY_VALUES:
        if re.search(rf"(?:^|[\W_]){cat}(?:[\W_]|$)", name_lower):
            return cat
    return "general"


PARSERS: dict[str, Callable[[bytes, str], tuple[str, str]]] = {
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".pdf": parse_pdf,
    ".docx": parse_docx,
}

SUPPORTED_EXTENSIONS: set[str] = set(PARSERS.keys())
