from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AuthorCandidate:
    name: str


@dataclass(slots=True)
class PaperCandidate:
    title: str
    detail_url: str | None = None
    pdf_url: str | None = None
    abstract: str | None = None
    session_name: str | None = None
    authors: list[AuthorCandidate] = field(default_factory=list)


@dataclass(slots=True)
class CrawlResult:
    discovered: int
    paper_candidates: list[PaperCandidate]


@dataclass(slots=True)
class ParsedPaper:
    full_text: str
    abstract_text: str | None
    page_count: int
    content_hash: str
    chunks: list[str]


@dataclass(slots=True)
class ArtifactRecord:
    artifact_kind: str
    label: str | None
    resolution_reason: str | None
    source_url: str
    resolved_url: str | None
    downloadable: bool
    mime_type: str | None = None
