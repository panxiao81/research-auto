from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from research_auto.infrastructure.postgres.database import Database
from research_auto.infrastructure.postgres.repositories import PostgresReadRepository
from research_auto.interfaces.worker.runner import build_storage
from research_auto.interfaces.web.manual_pdf import (
    ManualPdfUploadError,
    ManualPdfUploadService,
)
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
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _to_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.lower() in {"1", "true", "yes", "on"}


def _manual_pdf_upload_service(request: Request) -> ManualPdfUploadService:
    service = getattr(request.app.state, "manual_pdf_upload_service", None)
    if service is not None:
        return service
    return ManualPdfUploadService(
        repository=request.app.state.job_repository,
        storage=_artifact_storage(request),
        queue=request.app.state.job_repository,
    )


def _artifact_storage(request: Request):
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        storage = build_storage(request.app.state.settings)
        request.app.state.storage = storage
    return storage


def _paper_detail_or_404(request: Request, paper_id: str) -> dict[str, object]:
    detail = get_paper_detail_for_ui(request.app.state.db, paper_id)
    if detail.get("paper") is None:
        raise HTTPException(status_code=404)
    return detail


async def _read_upload_bytes(pdf: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await pdf.read(1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise ManualPdfUploadError("Uploaded file is too large.")
        chunks.append(chunk)


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
    detail = _paper_detail_or_404(request, paper_id)
    return templates.TemplateResponse(request, "pages/paper_detail.html", detail)


@router.post("/ui/papers/{paper_id}/upload-pdf", response_class=HTMLResponse)
async def ui_upload_paper_pdf(
    request: Request,
    paper_id: str,
    pdf: UploadFile = File(...),
) -> HTMLResponse:
    detail = _paper_detail_or_404(request, paper_id)
    if detail["paper"].get("best_pdf_url"):
        detail["upload_error"] = "Manual PDF upload is only available for unresolved papers."
        return templates.TemplateResponse(
            request,
            "pages/paper_detail.html",
            detail,
            status_code=409,
        )
    try:
        content = await _read_upload_bytes(pdf)
        _manual_pdf_upload_service(request).upload_pdf(
            paper_id=paper_id,
            file_name=pdf.filename or "upload.pdf",
            content_type=pdf.content_type,
            content=content,
        )
    except ManualPdfUploadError as exc:
        detail["upload_error"] = str(exc)
        detail["show_upload_retry"] = True
        return templates.TemplateResponse(
            request,
            "pages/paper_detail.html",
            detail,
            status_code=400,
        )
    return RedirectResponse(url=f"/ui/papers/{paper_id}", status_code=303)


@router.get("/ui/papers/{paper_id}/artifacts/{artifact_id}")
def ui_stream_artifact(request: Request, paper_id: str, artifact_id: str) -> StreamingResponse:
    artifact = request.app.state.job_repository.get_stored_artifact(
        paper_id=paper_id,
        artifact_id=artifact_id,
    )
    if artifact is None:
        raise HTTPException(status_code=404)
    try:
        stream = _artifact_storage(request).read(storage_uri=artifact.storage_uri)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502) from exc
    return StreamingResponse(
        stream,
        media_type=artifact.mime_type or "application/octet-stream",
    )


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
