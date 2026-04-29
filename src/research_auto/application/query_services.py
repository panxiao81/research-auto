from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Protocol

from research_auto.application.llm_types import (
    QuestionAnswer,
    fallback_answer_from_summary,
)


@dataclass(slots=True)
class Page:
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        return max(1, ceil(self.total / self.page_size)) if self.page_size else 1


class ReadRepository(Protocol):
    def list_papers(
        self,
        *,
        page: int,
        page_size: int,
        q: str | None,
        resolved: bool | None,
        has_pdf: bool | None,
        parsed: bool | None,
        summarized: bool | None,
        provider: str | None,
        starred: bool | None,
        sort: str,
        order: str,
    ) -> Page: ...
    def get_paper_detail(self, *, paper_id: str) -> dict[str, Any]: ...
    def search_papers(
        self, *, q: str, limit: int, starred: bool | None
    ) -> list[dict[str, Any]]: ...
    def get_stats(self) -> dict[str, Any]: ...
    def list_jobs(
        self, *, status: str | None, job_type: str | None, limit: int
    ) -> list[dict[str, Any]]: ...
    def list_conferences(self) -> list[dict[str, Any]]: ...
    def list_tracks(self) -> list[dict[str, Any]]: ...
    def list_api_papers(self, *, limit: int) -> list[dict[str, Any]]: ...
    def get_api_paper(self, *, paper_id: str) -> dict[str, Any]: ...
    def get_paper_question_context(
        self, *, paper_id: str, question: str, limit: int
    ) -> tuple[list[str], dict[str, Any] | None]: ...
    def get_library_question_context(
        self, *, question: str, limit: int
    ) -> tuple[list[str], dict[str, Any] | None, list[dict[str, Any]]]: ...


class QuestionAnswerer(Protocol):
    def answer_question(
        self, *, question: str, paper_context: str, chunk_quotes: list[str]
    ) -> QuestionAnswer: ...


class ReadQueryService:
    def __init__(self, repository: ReadRepository) -> None:
        self.repository = repository

    def list_papers(self, **kwargs: Any) -> Page:
        return self.repository.list_papers(**kwargs)

    def get_paper_detail(self, paper_id: str) -> dict[str, Any]:
        detail = self.repository.get_paper_detail(paper_id=paper_id)
        if not detail.get("paper"):
            detail["bibtex"] = ""
            return detail
        detail["bibtex"] = build_bibtex_for_ui(
            paper=detail["paper"], authors=detail["authors"]
        )
        return detail

    def search_papers(
        self, query: str, limit: int, starred: bool | None = None
    ) -> list[dict[str, Any]]:
        return self.repository.search_papers(q=query, limit=limit, starred=starred)

    def get_stats(self) -> dict[str, Any]:
        return self.repository.get_stats()

    def list_jobs(
        self, *, status: str | None, job_type: str | None, limit: int
    ) -> list[dict[str, Any]]:
        return self.repository.list_jobs(status=status, job_type=job_type, limit=limit)

    def list_conferences(self) -> list[dict[str, Any]]:
        return self.repository.list_conferences()

    def list_tracks(self) -> list[dict[str, Any]]:
        return self.repository.list_tracks()

    def list_api_papers(self, *, limit: int) -> list[dict[str, Any]]:
        return self.repository.list_api_papers(limit=limit)

    def get_api_paper(self, *, paper_id: str) -> dict[str, Any]:
        return self.repository.get_api_paper(paper_id=paper_id)


class QuestionAnswerService:
    def __init__(self, repository: ReadRepository, answerer: QuestionAnswerer) -> None:
        self.repository = repository
        self.answerer = answerer

    def ask_paper(self, *, paper_id: str, question: str, limit: int) -> dict[str, Any]:
        context_chunks, summary = self.repository.get_paper_question_context(
            paper_id=paper_id, question=question, limit=limit
        )
        try:
            answer = self.answerer.answer_question(
                question=question,
                paper_context="\n\n---\n\n".join(context_chunks),
                chunk_quotes=context_chunks,
            )
        except Exception:
            answer = fallback_answer_from_summary(
                question=question, summary_row=summary, chunk_quotes=context_chunks
            )
        return {
            "answer": answer.answer,
            "answer_zh": answer.answer_zh,
            "evidence_quotes": answer.evidence_quotes,
            "confidence": answer.confidence,
        }

    def ask_library(self, *, question: str, limit: int) -> dict[str, Any]:
        context_chunks, summary, papers = self.repository.get_library_question_context(
            question=question, limit=limit
        )
        try:
            answer = self.answerer.answer_question(
                question=question,
                paper_context="\n\n---\n\n".join(context_chunks),
                chunk_quotes=context_chunks,
            )
        except Exception:
            answer = fallback_answer_from_summary(
                question=question, summary_row=summary, chunk_quotes=context_chunks
            )
        return {
            "answer": answer.answer,
            "answer_zh": answer.answer_zh,
            "evidence_quotes": answer.evidence_quotes,
            "confidence": answer.confidence,
            "papers": dedupe_papers(papers),
        }


def build_bibtex_for_ui(*, paper: dict[str, Any], authors: list[dict[str, Any]]) -> str:
    cite_key = build_bibtex_key(paper=paper, authors=authors)
    fields: list[tuple[str, str]] = []
    author_names = [
        str(author.get("display_name") or "").strip()
        for author in authors
        if str(author.get("display_name") or "").strip()
    ]
    if author_names:
        fields.append(("author", " and ".join(author_names)))
    fields.append(("title", str(paper.get("canonical_title") or "")))
    fields.append(("booktitle", str(paper.get("conference_name") or "")))
    fields.append(("year", str(paper.get("year") or "")))
    doi = str(paper.get("doi") or "").strip()
    if doi:
        fields.append(("doi", doi))
    url = str(
        paper.get("best_landing_url")
        or paper.get("best_pdf_url")
        or paper.get("detail_url")
        or ""
    ).strip()
    if url:
        fields.append(("url", url))
    rendered_fields = []
    for key, value in fields:
        escaped = value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        rendered_fields.append(f"  {key} = {{{escaped}}}")
    return "@inproceedings{" + cite_key + ",\n" + ",\n".join(rendered_fields) + "\n}"


def build_bibtex_key(*, paper: dict[str, Any], authors: list[dict[str, Any]]) -> str:
    first_author = "paper"
    if authors:
        first_name = str(authors[0].get("display_name") or "").strip().split()
        if first_name:
            first_author = sanitize_bibtex_token(first_name[-1])
    year = str(paper.get("year") or "")
    title_words = [
        sanitize_bibtex_token(part)
        for part in str(paper.get("canonical_title") or "").split()
    ]
    title_token = next((word for word in title_words if word), "paper")
    return f"{first_author}{year}{title_token}"


def sanitize_bibtex_token(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isalnum())
    return cleaned[:32].lower()


def dedupe_papers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        paper_id = str(row["paper_id"])
        if paper_id in seen:
            continue
        seen.add(paper_id)
        result.append(
            {"paper_id": row["paper_id"], "canonical_title": row["canonical_title"]}
        )
    return result
