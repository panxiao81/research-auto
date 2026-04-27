from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from research_auto.application.admin_actions import (
    bootstrap_db as bootstrap_db_action,
    drain_worker as drain_worker_action,
    enqueue_parse as enqueue_parse_action,
    enqueue_resolve as enqueue_resolve_action,
    enqueue_resummarize_fallbacks as enqueue_resummarize_fallbacks_action,
    enqueue_summarize as enqueue_summarize_action,
    repair_resolution_status as repair_resolution_status_action,
    repair_running_jobs as repair_running_jobs_action,
    seed_icse as seed_icse_action,
)
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
ADMIN_DEFAULT_LIMIT = 50
ADMIN_DEFAULT_RUNNING_JOB_AGE_SECONDS = 900


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


def _admin_context(
    request: Request,
    *,
    result: dict[str, object] | None = None,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "result": result,
        "error": error,
        "defaults": {
            "resolve_limit": ADMIN_DEFAULT_LIMIT,
            "parse_limit": ADMIN_DEFAULT_LIMIT,
            "summarize_limit": ADMIN_DEFAULT_LIMIT,
            "fallback_limit": ADMIN_DEFAULT_LIMIT,
            "running_job_age_seconds": ADMIN_DEFAULT_RUNNING_JOB_AGE_SECONDS,
            "drain_queue": request.app.state.settings.worker_queue,
        },
    }


def _parse_limit(value: object, *, default: int | None = None) -> int | None:
    text = str(value or "").strip()
    if not text:
        return default
    return int(text)


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


@router.get("/ui/admin", response_class=HTMLResponse)
def ui_admin(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "pages/admin.html",
        _admin_context(request),
    )


@router.post("/ui/admin", response_class=HTMLResponse)
async def ui_admin_action(request: Request) -> HTMLResponse:
    form = await request.form()
    action = str(form.get("action") or "").strip()
    settings = request.app.state.settings
    result: dict[str, object] | None = None
    error: str | None = None

    try:
        if action == "bootstrap-db":
            bootstrap_db_action(settings)
            result = {"kind": "success", "message": "Database bootstrapped."}
        elif action == "seed-icse":
            seeded = seed_icse_action(settings)
            result = {
                "kind": "success",
                "message": f"Seeded {seeded['conference_slug']} / {seeded['track_slug']}.",
            }
        elif action == "resolve":
            limit = _parse_limit(form.get("limit"), default=None)
            count = enqueue_resolve_action(settings, limit)
            result = {"kind": "success", "message": f"Enqueued {count} resolve jobs."}
        elif action == "parse":
            limit = _parse_limit(form.get("limit"), default=None)
            count = enqueue_parse_action(settings, limit)
            result = {"kind": "success", "message": f"Enqueued {count} parse jobs."}
        elif action == "summarize":
            limit = _parse_limit(form.get("limit"), default=None)
            count = enqueue_summarize_action(settings, limit)
            result = {"kind": "success", "message": f"Enqueued {count} summarize jobs."}
        elif action == "resummarize-fallbacks":
            limit = _parse_limit(form.get("limit"), default=None)
            count = enqueue_resummarize_fallbacks_action(settings, limit)
            result = {
                "kind": "success",
                "message": f"Enqueued {count} fallback re-summarize jobs.",
            }
        elif action == "repair-resolution-status":
            repaired = repair_resolution_status_action(settings)
            result = {"kind": "success", "message": f"Repaired {repaired} papers."}
        elif action == "repair-running-jobs":
            older_than_seconds = _parse_limit(
                form.get("older_than_seconds"),
                default=ADMIN_DEFAULT_RUNNING_JOB_AGE_SECONDS,
            )
            assert older_than_seconds is not None
            repaired = repair_running_jobs_action(settings, older_than_seconds)
            result = {
                "kind": "success",
                "message": (
                    f"Repaired {repaired} running jobs older than {older_than_seconds} seconds."
                ),
            }
        elif action == "drain":
            queue = str(form.get("queue") or "").strip() or None
            processed = drain_worker_action(settings, queue)
            result = {"kind": "success", "message": f"Processed {processed} jobs."}
        else:
            raise ValueError("Unknown admin action.")
    except Exception as exc:  # noqa: BLE001
        error = str(exc)

    status_code = 200 if error is None else 400
    return templates.TemplateResponse(
        request,
        "pages/admin.html",
        _admin_context(request, result=result, error=error),
        status_code=status_code,
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
