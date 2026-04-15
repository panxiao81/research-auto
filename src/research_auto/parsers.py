from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


PARSER_VERSION = "pdf-v1"


@dataclass(slots=True)
class ParsedPaper:
    full_text: str
    abstract_text: str | None
    page_count: int
    content_hash: str
    chunks: list[str]


def parse_pdf_file(path: str) -> ParsedPaper:
    reader = PdfReader(path)
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = normalize_text(text)
        if text:
            pages.append(text)
    full_text = "\n\n".join(pages).strip()
    content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    abstract = extract_abstract(full_text)
    chunks = chunk_text(full_text)
    return ParsedPaper(
        full_text=full_text,
        abstract_text=abstract,
        page_count=len(reader.pages),
        content_hash=content_hash,
        chunks=chunks,
    )


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_abstract(full_text: str) -> str | None:
    match = re.search(r"\bAbstract\b\s*(.+?)(?:\n\s*\n|\b1\s+Introduction\b|\bIntroduction\b)", full_text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    abstract = normalize_text(match.group(1))
    return abstract[:8000] if abstract else None


def chunk_text(full_text: str, *, max_chars: int = 5000, overlap_chars: int = 500) -> list[str]:
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
