from __future__ import annotations

from research_auto.infrastructure.postgres.database import Database
from research_auto.infrastructure.postgres.repositories import PostgresReadRepository
from research_auto.application.query_services import Page, ReadQueryService


def _service(db: Database) -> ReadQueryService:
    return ReadQueryService(PostgresReadRepository(db))


def list_papers_for_ui(
    db: Database,
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
) -> Page:
    return _service(db).list_papers(
        page=page,
        page_size=page_size,
        q=q,
        resolved=resolved,
        has_pdf=has_pdf,
        parsed=parsed,
        summarized=summarized,
        provider=provider,
        starred=starred,
        sort=sort,
        order=order,
    )


def get_paper_detail_for_ui(db: Database, paper_id: str) -> dict[str, object]:
    return _service(db).get_paper_detail(paper_id)


def search_papers_for_ui(
    db: Database, q: str, limit: int, starred: bool | None = None
) -> list[dict[str, object]]:
    return _service(db).search_papers(q, limit, starred=starred)


def get_ui_stats(db: Database) -> dict[str, object]:
    return _service(db).get_stats()


def list_jobs_for_ui(
    db: Database, *, status: str | None, job_type: str | None, limit: int
) -> list[dict[str, object]]:
    return _service(db).list_jobs(status=status, job_type=job_type, limit=limit)
