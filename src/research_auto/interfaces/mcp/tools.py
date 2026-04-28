from __future__ import annotations

from typing import Any

from research_auto.application.query_services import ReadQueryService

MAX_PAPER_SEARCH_LIMIT = 20
MAX_CONTEXT_LIMIT = 12


class McpPaperLookupError(LookupError):
    pass


def _validate_query(query: str) -> str:
    normalized = query.strip()
    if not normalized:
        raise ValueError("query must not be empty")
    return normalized


def _clamp_limit(limit: int, *, default: int, maximum: int) -> int:
    if limit <= 0:
        return default
    return min(limit, maximum)


def search_papers_tool(
    *, read_service: ReadQueryService, query: str, limit: int = 10
) -> dict[str, Any]:
    normalized_query = _validate_query(query)
    normalized_limit = _clamp_limit(limit, default=10, maximum=MAX_PAPER_SEARCH_LIMIT)
    return {
        "query": normalized_query,
        "limit": normalized_limit,
        "results": read_service.search_papers(normalized_query, normalized_limit),
    }


def get_paper_tool(*, read_service: ReadQueryService, paper_id: str) -> dict[str, Any]:
    detail = read_service.get_paper_detail(paper_id)
    if not detail.get("paper"):
        raise McpPaperLookupError(paper_id)
    return detail


def search_context_tool(
    *, repository: Any, query: str, paper_id: str | None = None, limit: int = 8
) -> dict[str, Any]:
    normalized_query = _validate_query(query)
    normalized_limit = _clamp_limit(limit, default=8, maximum=MAX_CONTEXT_LIMIT)
    if paper_id:
        chunks, summary = repository.get_paper_question_context(
            paper_id=paper_id,
            question=normalized_query,
            limit=normalized_limit,
        )
        return {
            "scope": "paper",
            "paper_id": paper_id,
            "limit": normalized_limit,
            "summary": summary,
            "chunks": chunks,
        }
    chunks, summary, papers = repository.get_library_question_context(
        question=normalized_query,
        limit=normalized_limit,
    )
    normalized_chunks = []
    for index, chunk in enumerate(chunks):
        row = papers[index] if index < len(papers) else {}
        normalized_chunks.append(
            {
                "paper_id": row.get("paper_id"),
                "canonical_title": row.get("canonical_title"),
                "content": chunk,
            }
        )
    deduped_chunks = []
    seen: set[tuple[object, object]] = set()
    for chunk in normalized_chunks:
        key = (chunk["paper_id"], chunk["content"])
        if key in seen:
            continue
        seen.add(key)
        deduped_chunks.append(chunk)
    return {
        "scope": "library",
        "paper_id": None,
        "limit": normalized_limit,
        "summary": summary,
        "chunks": deduped_chunks,
    }
