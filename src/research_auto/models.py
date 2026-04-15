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
