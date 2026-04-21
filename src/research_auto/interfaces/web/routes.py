from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from research_auto.infrastructure.postgres.database import Database
from research_auto.infrastructure.postgres.repositories import PostgresReadRepository
from research_auto.interfaces.web.services import (
    get_paper_detail_for_ui,
    get_ui_stats,
    list_jobs_for_ui,
    list_papers_for_ui,
    search_papers_for_ui,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
templates = Jinja2Templates(directory=str(REPO_ROOT / "templates"))

router = APIRouter(include_in_schema=False)


def _to_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.lower() in {"1", "true", "yes", "on"}


@router.get("/ui", response_class=HTMLResponse)
def ui_home(request: Request) -> HTMLResponse:
    db: Database = request.app.state.db
    stats = get_ui_stats(db)
    recent = list_papers_for_ui(
        db,
        page=1,
        page_size=10,
        q=None,
        resolved=None,
        has_pdf=None,
        parsed=None,
        summarized=True,
        provider=None,
        sort="updated",
        order="desc",
    )
    return templates.TemplateResponse(
        request,
        "pages/home.html",
        {"stats": stats, "recent_papers": recent.items},
    )


@router.get("/ui/papers", response_class=HTMLResponse)
def ui_papers(
    request: Request,
    q: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "ready",
    order: str = "desc",
    resolved: str | None = None,
    has_pdf: str | None = None,
    parsed: str | None = None,
    summarized: str | None = None,
    provider: str | None = None,
) -> HTMLResponse:
    db: Database = request.app.state.db
    result = list_papers_for_ui(
        db,
        page=page,
        page_size=page_size,
        q=q,
        resolved=_to_bool(resolved),
        has_pdf=_to_bool(has_pdf),
        parsed=_to_bool(parsed),
        summarized=_to_bool(summarized),
        provider=provider,
        sort=sort,
        order=order,
    )
    providers = PostgresReadRepository(db).list_summary_providers()
    return templates.TemplateResponse(
        request,
        "pages/papers.html",
        {
            "result": result,
            "q": q or "",
            "sort": sort,
            "order": order,
            "filters": {
                "resolved": resolved or "",
                "has_pdf": has_pdf or "",
                "parsed": parsed or "",
                "summarized": summarized or "",
                "provider": provider or "",
            },
            "providers": providers,
        },
    )


@router.get("/ui/papers/{paper_id}", response_class=HTMLResponse)
def ui_paper_detail(request: Request, paper_id: str) -> HTMLResponse:
    db: Database = request.app.state.db
    detail = get_paper_detail_for_ui(db, paper_id)
    return templates.TemplateResponse(request, "pages/paper_detail.html", detail)


@router.get("/ui/search", response_class=HTMLResponse)
def ui_search(request: Request, q: str = Query(""), limit: int = 20) -> HTMLResponse:
    db: Database = request.app.state.db
    results = search_papers_for_ui(db, q, limit) if q else []
    return templates.TemplateResponse(
        request, "pages/search.html", {"q": q, "results": results, "limit": limit}
    )


@router.get("/ui/stats", response_class=HTMLResponse)
def ui_stats(request: Request) -> HTMLResponse:
    db: Database = request.app.state.db
    stats = get_ui_stats(db)
    return templates.TemplateResponse(request, "pages/stats.html", {"stats": stats})


@router.get("/ui/jobs", response_class=HTMLResponse)
def ui_jobs(
    request: Request,
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 100,
) -> HTMLResponse:
    db: Database = request.app.state.db
    jobs = list_jobs_for_ui(db, status=status, job_type=job_type, limit=limit)
    return templates.TemplateResponse(
        request,
        "pages/jobs.html",
        {
            "jobs": jobs,
            "status": status or "",
            "job_type": job_type or "",
            "limit": limit,
        },
    )
