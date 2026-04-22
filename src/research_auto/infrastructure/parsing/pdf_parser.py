from __future__ import annotations

import hashlib
import re
from typing import BinaryIO

from pypdf import PdfReader

from research_auto.domain.records import ParsedPaper


PARSER_VERSION = "pypdf-v1"


def parse_pdf_file(source: str | BinaryIO) -> ParsedPaper:
    reader = PdfReader(source)
    source_pages: list[str] = []
    normalized_pages: list[str] = []
    for page in reader.pages:
        raw_text = sanitize_source_text(page.extract_text() or "")
        normalized_text = normalize_text(raw_text)
        if raw_text:
            source_pages.append(raw_text)
        if normalized_text:
            normalized_pages.append(normalized_text)
    source_text = "\n\n".join(source_pages).strip()
    full_text = "\n\n".join(normalized_pages).strip()
    content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    abstract = extract_abstract(full_text)
    chunks = chunk_text(full_text)
    return ParsedPaper(
        parser_version=PARSER_VERSION,
        source_text=source_text,
        full_text=full_text,
        abstract_text=abstract,
        page_count=len(reader.pages),
        content_hash=content_hash,
        chunks=chunks,
    )


def sanitize_source_text(text: str) -> str:
    return text.replace("\x00", " ").strip()


def normalize_text(text: str) -> str:
    text = sanitize_source_text(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_abstract(full_text: str) -> str | None:
    match = re.search(
        r"\bAbstract\b\s*(.+?)(?:\n\s*\n|\b1\s+Introduction\b|\bIntroduction\b)",
        full_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    abstract = normalize_text(match.group(1))
    return abstract[:8000] if abstract else None


def chunk_text(
    full_text: str, *, max_chars: int = 5000, overlap_chars: int = 500
) -> list[str]:
    if len(full_text) <= max_chars:
        return [full_text] if full_text else []
    chunks: list[str] = []
    start = 0
    while start < len(full_text):
        end = min(len(full_text), start + max_chars)
        if end < len(full_text):
            split = full_text.rfind("\n\n", start, end)
            if split > start + 1000:
                end = split
        chunk = full_text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(full_text):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks
