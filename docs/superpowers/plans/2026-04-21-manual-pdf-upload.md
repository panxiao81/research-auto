# Manual PDF Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator upload a PDF from a paper detail page, persist it as that paper's primary artifact, and enqueue parsing immediately.

**Architecture:** Add a small web-facing manual upload service that reuses the existing storage adapter and job queue. Persist the uploaded PDF in the `artifacts` table, expose it through a stable app route, update the paper's `best_pdf_url` and `resolution_status`, and keep the route layer thin by moving DB and queue orchestration into a focused helper.

**Tech Stack:** FastAPI, Jinja2 SSR templates, PostgreSQL repositories, existing artifact storage adapters, pytest

---

## File Map

- Modify: `src/research_auto/interfaces/api/app.py`
  Wire storage and job repository into `app.state` so web routes can reuse the same adapter construction path as workers.
- Create: `src/research_auto/interfaces/web/manual_pdf.py`
  Keep manual upload orchestration in one place: validate file, store content, persist artifact metadata, update paper state, enqueue parse, and read stored files back.
- Modify: `src/research_auto/infrastructure/postgres/repositories.py`
  Add one focused write method for manual PDF ingestion and one read method for locating a stored artifact by paper and artifact id.
- Modify: `src/research_auto/interfaces/web/routes.py`
  Add `POST /ui/papers/{paper_id}/upload-pdf` and `GET /ui/papers/{paper_id}/artifacts/{artifact_id}`.
- Modify: `templates/pages/paper_detail.html`
  Render the upload form, validation message area, and uploaded PDF link.
- Modify: `tests/test_frontend.py`
  Cover the new upload form, the upload POST path, rejection of non-PDF uploads, and artifact streaming.
- Create: `tests/test_manual_pdf_upload.py`
  Unit-test the new manual upload helper with fake storage, fake queue, and fake repository.

### Task 1: Build the Manual Upload Helper First

**Files:**
- Create: `src/research_auto/interfaces/web/manual_pdf.py`
- Test: `tests/test_manual_pdf_upload.py`

- [ ] **Step 1: Write the failing helper test for a successful upload**

```python
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from research_auto.application.storage_types import StorageWriteResult
from research_auto.interfaces.web.manual_pdf import ManualPdfUploadService


class FakeStorage:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, bytes, str | None]] = []

    def write(
        self,
        *,
        paper_id: str,
        file_name: str,
        content: bytes,
        mime_type: str | None,
    ) -> StorageWriteResult:
        self.writes.append((paper_id, file_name, content, mime_type))
        return StorageWriteResult(
            storage_uri=f"local://{paper_id}/{file_name}",
            storage_key=f"{paper_id}/{file_name}",
            byte_size=len(content),
            mime_type=mime_type,
            checksum_sha256="abc123",
        )

    def read(self, *, storage_uri: str) -> BytesIO:
        return BytesIO(b"%PDF-1.4\n")


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, object]] = []

    def enqueue(self, **kwargs: object) -> None:
        self.enqueued.append(kwargs)


@dataclass
class SavedArtifact:
    id: str
    serving_url: str
    storage_uri: str
    mime_type: str


class FakeRepository:
    def __init__(self) -> None:
        self.saved_calls: list[dict[str, object]] = []

    def save_manual_pdf(
        self,
        *,
        paper_id: str,
        file_name: str,
        storage_uri: str,
        storage_key: str,
        mime_type: str | None,
        checksum_sha256: str,
        byte_size: int,
        serving_url: str,
    ) -> SavedArtifact:
        self.saved_calls.append(
            {
                "paper_id": paper_id,
                "file_name": file_name,
                "storage_uri": storage_uri,
                "serving_url": serving_url,
            }
        )
        return SavedArtifact(
            id="artifact-1",
            serving_url=serving_url,
            storage_uri=storage_uri,
            mime_type=mime_type or "application/pdf",
        )


def test_upload_pdf_stores_artifact_and_enqueues_parse() -> None:
    service = ManualPdfUploadService(repository=FakeRepository(), storage=FakeStorage(), queue=FakeQueue())

    result = service.upload_pdf(
        paper_id="paper-1",
        file_name="notes.pdf",
        content_type="application/pdf",
        content=b"%PDF-1.4\nbody",
    )

    assert result.artifact_id == "artifact-1"
    assert result.serving_url == "/ui/papers/paper-1/artifacts/artifact-1"
    assert service.storage.writes == [("paper-1", "notes.pdf", b"%PDF-1.4\nbody", "application/pdf")]
    assert service.queue.enqueued[0]["job_type"] == "parse_artifact"
    assert service.queue.enqueued[0]["payload"] == {
        "paper_id": "paper-1",
        "artifact_id": "artifact-1",
        "storage_uri": "local://paper-1/notes.pdf",
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_manual_pdf_upload.py::test_upload_pdf_stores_artifact_and_enqueues_parse -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `research_auto.interfaces.web.manual_pdf`

- [ ] **Step 3: Add the minimal helper implementation**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ManualPdfUploadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ManualPdfUploadResult:
    artifact_id: str
    serving_url: str


class ManualPdfUploadService:
    def __init__(self, *, repository, storage, queue) -> None:
        self.repository = repository
        self.storage = storage
        self.queue = queue

    def upload_pdf(
        self,
        *,
        paper_id: str,
        file_name: str,
        content_type: str | None,
        content: bytes,
    ) -> ManualPdfUploadResult:
        normalized_name = Path(file_name or "upload.pdf").name or "upload.pdf"
        if not normalized_name.lower().endswith(".pdf"):
            normalized_name = f"{normalized_name}.pdf"
        if not content.startswith(b"%PDF"):
            raise ManualPdfUploadError("Uploaded file is not a PDF.")
        stored = self.storage.write(
            paper_id=paper_id,
            file_name=normalized_name,
            content=content,
            mime_type=content_type or "application/pdf",
        )
        saved = self.repository.save_manual_pdf(
            paper_id=paper_id,
            file_name=normalized_name,
            storage_uri=stored.storage_uri,
            storage_key=stored.storage_key,
            mime_type=stored.mime_type,
            checksum_sha256=stored.checksum_sha256,
            byte_size=stored.byte_size,
        )
        final_url = f"/ui/papers/{paper_id}/artifacts/{saved.id}"
        self.queue.enqueue(
            job_type="parse_artifact",
            payload={
                "paper_id": paper_id,
                "artifact_id": saved.id,
                "storage_uri": saved.storage_uri,
            },
            dedupe_key=f"parse_artifact:{saved.id}",
            priority=40,
            max_attempts=5,
        )
        return ManualPdfUploadResult(artifact_id=saved.id, serving_url=final_url)
```

Replace the double-save stub immediately with the final shape before moving on. The real helper should calculate the artifact serving URL after the insert returns the new id, then call a dedicated repository method that persists the row and updates the paper in one transaction.

- [ ] **Step 4: Tighten the helper with validation tests before touching routes**

Add these tests to `tests/test_manual_pdf_upload.py`:

```python
def test_upload_pdf_rejects_non_pdf_bytes() -> None:
    service = ManualPdfUploadService(repository=FakeRepository(), storage=FakeStorage(), queue=FakeQueue())

    with pytest.raises(ManualPdfUploadError, match="not a PDF"):
        service.upload_pdf(
            paper_id="paper-1",
            file_name="notes.pdf",
            content_type="application/pdf",
            content=b"plain text",
        )


def test_upload_pdf_appends_pdf_extension_when_missing() -> None:
    storage = FakeStorage()
    service = ManualPdfUploadService(repository=FakeRepository(), storage=storage, queue=FakeQueue())

    service.upload_pdf(
        paper_id="paper-1",
        file_name="camera-ready",
        content_type="application/pdf",
        content=b"%PDF-1.4\nbody",
    )

    assert storage.writes[0][1] == "camera-ready.pdf"
```

- [ ] **Step 5: Run the helper tests to green**

Run: `uv run pytest tests/test_manual_pdf_upload.py -v`
Expected: PASS for all helper tests


### Task 2: Add Repository Support for Manual PDF Persistence

**Files:**
- Modify: `src/research_auto/infrastructure/postgres/repositories.py`
- Modify: `src/research_auto/interfaces/web/manual_pdf.py`
- Test: `tests/test_manual_pdf_upload.py`

- [ ] **Step 1: Write the failing repository-facing unit test shape**

Update the fake repository contract in `tests/test_manual_pdf_upload.py` so the helper uses a single transaction-facing method and a lookup method:

```python
@dataclass
class SavedArtifact:
    id: str
    storage_uri: str
    mime_type: str


class FakeRepository:
    def __init__(self) -> None:
        self.saved_calls: list[dict[str, object]] = []

    def save_manual_pdf(
        self,
        *,
        paper_id: str,
        file_name: str,
        storage_uri: str,
        storage_key: str,
        mime_type: str | None,
        checksum_sha256: str,
        byte_size: int,
    ) -> SavedArtifact:
        self.saved_calls.append({"paper_id": paper_id, "file_name": file_name})
        return SavedArtifact(
            id="artifact-1",
            storage_uri=storage_uri,
            mime_type=mime_type or "application/pdf",
        )
```

Then adjust the success assertion to continue expecting the serving URL `/ui/papers/paper-1/artifacts/artifact-1` from the service.

- [ ] **Step 2: Run the helper tests to see the contract mismatch fail**

Run: `uv run pytest tests/test_manual_pdf_upload.py -v`
Expected: FAIL because `ManualPdfUploadService` still passes `serving_url` into `save_manual_pdf`

- [ ] **Step 3: Implement the repository method and matching service change**

In `src/research_auto/infrastructure/postgres/repositories.py`, add a small dataclass near the other repository-only types used by the web layer:

```python
@dataclass(frozen=True, slots=True)
class StoredArtifactRef:
    id: str
    storage_uri: str
    mime_type: str | None
```

Then add this method to `PostgresJobRepository`:

```python
def save_manual_pdf(
    self,
    *,
    paper_id: str,
    file_name: str,
    storage_uri: str,
    storage_key: str,
    mime_type: str | None,
    checksum_sha256: str,
    byte_size: int,
) -> StoredArtifactRef:
    with self.db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into artifacts (
                    paper_id, artifact_kind, label, resolution_reason, source_url,
                    resolved_url, mime_type, downloadable, download_status,
                    storage_uri, storage_key, checksum_sha256, byte_size, downloaded_at
                )
                values (
                    %s, 'manual_pdf', 'manual upload', 'manual_pdf_upload', %s,
                    %s, %s, true, 'downloaded',
                    %s, %s, %s, %s, now()
                )
                returning id, storage_uri, mime_type
                """,
                (
                    paper_id,
                    f"manual://{paper_id}/{file_name}",
                    f"manual://{paper_id}/{file_name}",
                    mime_type,
                    storage_uri,
                    storage_key,
                    checksum_sha256,
                    byte_size,
                ),
            )
            artifact = cur.fetchone()
            cur.execute(
                "update papers set best_pdf_url = %s, resolution_status = 'resolved' where id = %s",
                (f"/ui/papers/{paper_id}/artifacts/{artifact['id']}", paper_id),
            )
        conn.commit()
    return StoredArtifactRef(**artifact)
```

Also add a read method for the streaming route:

```python
def get_stored_artifact(self, *, paper_id: str, artifact_id: str) -> dict[str, Any] | None:
    return self.jobs.fetch_one(
        "select id, storage_uri, mime_type from artifacts where id = %s and paper_id = %s and storage_uri is not null",
        (artifact_id, paper_id),
    )
```

Update `ManualPdfUploadService` so it calls `save_manual_pdf(...)` once and computes `serving_url` from `saved.id` after the transaction returns.

- [ ] **Step 4: Run helper tests again**

Run: `uv run pytest tests/test_manual_pdf_upload.py -v`
Expected: PASS


### Task 3: Expose Upload and Artifact Routes in the Web App

**Files:**
- Modify: `src/research_auto/interfaces/api/app.py`
- Modify: `src/research_auto/interfaces/web/routes.py`
- Modify: `src/research_auto/interfaces/web/manual_pdf.py`
- Test: `tests/test_frontend.py`

- [ ] **Step 1: Write the failing frontend tests for POST upload and GET artifact**

Add these tests to `tests/test_frontend.py`:

```python
def test_ui_paper_detail_shows_manual_upload_for_unresolved_paper() -> None:
    client = _client()
    response = client.get("/ui/papers/0f7f0c2d-8e6a-4a8e-bdf0-b5f6f4d4c111")
    assert response.status_code == 200
    assert "Upload PDF manually" in response.text or "Open PDF" in response.text


def test_ui_upload_pdf_redirects_back_to_paper_detail() -> None:
    client = _client()
    response = client.post(
        "/ui/papers/0f7f0c2d-8e6a-4a8e-bdf0-b5f6f4d4c111/upload-pdf",
        files={"pdf": ("paper.pdf", b"%PDF-1.4\nbody", "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/papers/0f7f0c2d-8e6a-4a8e-bdf0-b5f6f4d4c111"


def test_ui_upload_pdf_rejects_non_pdf_upload() -> None:
    client = _client()
    response = client.post(
        "/ui/papers/0f7f0c2d-8e6a-4a8e-bdf0-b5f6f4d4c111/upload-pdf",
        files={"pdf": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert "Uploaded file is not a PDF" in response.text


def test_ui_artifact_route_streams_uploaded_pdf() -> None:
    client = _client()

    class Repo:
        def get_stored_artifact(self, *, paper_id: str, artifact_id: str) -> dict[str, str] | None:
            return {
                "id": artifact_id,
                "storage_uri": "local://paper-1/paper.pdf",
                "mime_type": "application/pdf",
            }

    class Storage:
        def read(self, *, storage_uri: str):
            from io import BytesIO

            return BytesIO(b"%PDF-1.4\nbody")

    client.app.state.job_repo = Repo()
    client.app.state.storage = Storage()
    response = client.get("/ui/papers/paper-1/artifacts/artifact-1")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
```

- [ ] **Step 2: Run the failing frontend tests**

Run: `uv run pytest tests/test_frontend.py -v`
Expected: FAIL with missing routes and missing upload form text

- [ ] **Step 3: Wire dependencies into the app and add routes**

Update `src/research_auto/interfaces/api/app.py` to reuse `build_storage()`:

```python
from research_auto.interfaces.worker.runner import build_storage
from research_auto.infrastructure.postgres.repositories import PostgresJobRepository


def create_app() -> FastAPI:
    settings = get_settings()
    db = Database(settings.database_url)
    storage = build_storage(settings)
    app = FastAPI(title="research-auto", version="0.1.0")
    app.state.db = db
    app.state.storage = storage
    app.state.job_repo = PostgresJobRepository(db)
```

Then update `src/research_auto/interfaces/web/routes.py`:

```python
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from research_auto.interfaces.web.manual_pdf import ManualPdfUploadError, ManualPdfUploadService


@router.post("/ui/papers/{paper_id}/upload-pdf")
async def ui_upload_pdf(request: Request, paper_id: str, pdf: UploadFile = File(...)) -> RedirectResponse | HTMLResponse:
    service = ManualPdfUploadService(
        repository=request.app.state.job_repo,
        storage=request.app.state.storage,
        queue=request.app.state.job_repo,
    )
    try:
        result = service.upload_pdf(
            paper_id=paper_id,
            file_name=pdf.filename or "upload.pdf",
            content_type=pdf.content_type,
            content=await pdf.read(),
        )
    except ManualPdfUploadError as exc:
        detail = get_paper_detail_for_ui(request.app.state.db, paper_id)
        detail["upload_error"] = str(exc)
        return templates.TemplateResponse(request, "pages/paper_detail.html", detail, status_code=400)
    return RedirectResponse(url=f"/ui/papers/{paper_id}", status_code=303)


@router.get("/ui/papers/{paper_id}/artifacts/{artifact_id}")
def ui_paper_artifact(request: Request, paper_id: str, artifact_id: str) -> StreamingResponse:
    artifact = request.app.state.job_repo.get_stored_artifact(paper_id=paper_id, artifact_id=artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404)
    stream = request.app.state.storage.read(storage_uri=artifact["storage_uri"])
    return StreamingResponse(stream, media_type=artifact["mime_type"] or "application/pdf")
```

- [ ] **Step 4: Run the frontend tests again**

Run: `uv run pytest tests/test_frontend.py -v`
Expected: PASS for the new upload and artifact route tests


### Task 4: Add the Paper Detail Form and Finish UX

**Files:**
- Modify: `templates/pages/paper_detail.html`
- Modify: `tests/test_frontend.py`

- [ ] **Step 1: Write the failing UI assertion for the form block**

Extend the paper detail test in `tests/test_frontend.py`:

```python
def test_ui_paper_detail_renders_summary() -> None:
    client = _client()
    response = client.get("/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49")
    assert response.status_code == 200
    assert "Structured Summary" in response.text
    assert "Citation" in response.text
    assert "@inproceedings{" in response.text
    assert "研究问题" in response.text
    assert "未来工作" in response.text
    assert "Upload PDF manually" in response.text or "Open PDF" in response.text
```

- [ ] **Step 2: Run the focused paper detail test**

Run: `uv run pytest tests/test_frontend.py::test_ui_paper_detail_renders_summary -v`
Expected: FAIL if the template still has no upload form block

- [ ] **Step 3: Add the template form and error message block**

Insert this card into `templates/pages/paper_detail.html` inside the metadata sidebar under the PDF row:

```html
{% if not paper.best_pdf_url %}
<div class="alert alert-warning mt-4 mb-0" role="alert">
  <div class="fw-semibold mb-2">Upload PDF manually</div>
  <p class="small mb-3">This paper does not have a usable PDF yet. Upload one to start parsing.</p>
  {% if upload_error %}
  <div class="text-danger small mb-2">{{ upload_error }}</div>
  {% endif %}
  <form method="post" action="/ui/papers/{{ paper.id }}/upload-pdf" enctype="multipart/form-data" class="d-grid gap-2">
    <input class="form-control form-control-sm" type="file" name="pdf" accept="application/pdf,.pdf" required>
    <button class="btn btn-sm btn-primary" type="submit">Upload PDF</button>
  </form>
</div>
{% endif %}
```

Keep the visual language Bootstrap-native and avoid changing the rest of the page layout.

- [ ] **Step 4: Run the frontend tests to verify the UI**

Run: `uv run pytest tests/test_frontend.py -v`
Expected: PASS


### Task 5: Final Verification

**Files:**
- Modify: `tests/test_frontend.py`
- Modify: `tests/test_manual_pdf_upload.py`

- [ ] **Step 1: Run the targeted upload-related tests together**

Run: `uv run pytest tests/test_manual_pdf_upload.py tests/test_frontend.py -v`
Expected: PASS

- [ ] **Step 2: Run the broader regression suite that covers the touched areas**

Run: `uv run pytest tests/test_job_executor.py tests/test_storage_adapters.py tests/test_frontend.py tests/test_manual_pdf_upload.py -v`
Expected: PASS

- [ ] **Step 3: Smoke-check the app manually if local services are available**

Run: `uv run research-auto serve api --host 127.0.0.1 --port 8000`
Expected: server starts; opening `/ui/papers/<paper-id>` shows either the existing PDF link or the new upload form, and a successful upload redirects back to the paper detail page.

- [ ] **Step 4: Commit the completed feature**

```bash
git add src/research_auto/interfaces/api/app.py \
  src/research_auto/interfaces/web/manual_pdf.py \
  src/research_auto/interfaces/web/routes.py \
  src/research_auto/infrastructure/postgres/repositories.py \
  templates/pages/paper_detail.html \
  tests/test_frontend.py \
  tests/test_manual_pdf_upload.py
git commit -m "feat: add manual pdf upload for unresolved papers"
```
