from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
import pytest
from pypdf import PdfWriter

from research_auto.interfaces.api.app import create_app
from research_auto.interfaces.web import routes as web_routes
from research_auto.interfaces.web.manual_pdf import ManualPdfUploadService


def _unresolved_paper_detail(
    *, paper_id: str = "paper-1", upload_error: str | None = None
) -> dict[str, object]:
    detail: dict[str, object] = {
        "paper": {
            "id": paper_id,
            "canonical_title": "Upload Needed",
            "conference_name": "ICSE 2026",
            "track_name": "Research Track",
            "year": 2026,
            "session_name": None,
            "resolution_status": "unresolved",
            "best_pdf_url": None,
            "best_landing_url": None,
            "detail_url": None,
            "abstract": None,
        },
        "authors": [{"display_name": "Ada Lovelace"}],
        "artifacts": [],
        "parse": None,
        "chunks": [],
        "summary": None,
        "bibtex": "",
    }
    if upload_error is not None:
        detail["upload_error"] = upload_error
    return detail


def _client() -> TestClient:
    return TestClient(create_app())


def _pdf_bytes() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


class _FakeUploadService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def upload_pdf(
        self,
        *,
        paper_id: str,
        file_name: str,
        content_type: str | None,
        content: bytes,
    ) -> object:
        self.calls.append(
            {
                "paper_id": paper_id,
                "file_name": file_name,
                "content_type": content_type,
                "content": content,
            }
        )
        if self.error is not None:
            raise self.error

        class _Result:
            artifact_id = "artifact-1"
            serving_url = f"/ui/papers/{paper_id}/artifacts/artifact-1"

        return _Result()


class _FakeJobRepository:
    def __init__(self, artifact: object | None = None) -> None:
        self.artifact = artifact
        self.calls: list[dict[str, str]] = []

    def get_stored_artifact(self, *, paper_id: str, artifact_id: str) -> object | None:
        self.calls.append({"paper_id": paper_id, "artifact_id": artifact_id})
        return self.artifact


class _FakeStorage:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[str] = []

    def read(self, *, storage_uri: str) -> BytesIO:
        self.calls.append(storage_uri)
        return BytesIO(self.content)


class _MissingStorage:
    def read(self, *, storage_uri: str) -> BytesIO:
        raise FileNotFoundError(storage_uri)


class _BrokenStorage:
    def read(self, *, storage_uri: str) -> BytesIO:
        raise RuntimeError("storage backend unavailable")


class _FakeWriteStorage:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, bytes, str | None]] = []

    def write(
        self,
        *,
        paper_id: str,
        file_name: str,
        content: bytes,
        mime_type: str | None,
    ):
        self.writes.append((paper_id, file_name, content, mime_type))
        return type(
            "StorageWriteResult",
            (),
            {
                "storage_uri": f"local://{paper_id}/{file_name}",
                "storage_key": f"{paper_id}/{file_name}",
                "byte_size": len(content),
                "mime_type": mime_type,
                "checksum_sha256": "abc123",
            },
        )()


class _FakeSaveRepository:
    def __init__(self) -> None:
        self.saved_calls: list[dict[str, object]] = []
        self.enqueued: list[dict[str, object]] = []

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
    ):
        self.saved_calls.append(
            {
                "paper_id": paper_id,
                "file_name": file_name,
                "storage_uri": storage_uri,
                "storage_key": storage_key,
                "mime_type": mime_type,
                "checksum_sha256": checksum_sha256,
                "byte_size": byte_size,
            }
        )
        self.enqueued.append(
            {
                "job_type": "parse_artifact",
                "payload": {
                    "paper_id": paper_id,
                    "artifact_id": "artifact-1",
                    "storage_uri": storage_uri,
                    "checksum_sha256": checksum_sha256,
                },
            }
        )
        return type(
            "StoredArtifact",
            (),
            {
                "id": "artifact-1",
                "storage_uri": storage_uri,
                "mime_type": mime_type or "application/pdf",
            },
        )()

    def enqueue(self, **kwargs: object) -> None:
        self.enqueued.append(kwargs)


class _FakeEnqueueQueue:
    def enqueue(self, **kwargs: object) -> None:
        return None


def test_ui_home_renders() -> None:
    client = _client()
    response = client.get("/ui")
    assert response.status_code == 200
    assert "Paper Library" in response.text
    assert "Recent Ready Papers" in response.text


def test_ui_papers_sheet_renders() -> None:
    client = _client()
    response = client.get("/ui/papers")
    assert response.status_code == 200
    assert "Sheet view for browsing papers" in response.text
    assert "Summarized first" in response.text


def test_ui_search_renders_results() -> None:
    client = _client()
    response = client.get("/ui/search?q=single+tester+limits")
    assert response.status_code == 200
    assert "Search" in response.text
    assert "Breaking Single-Tester Limits" in response.text


def test_ui_paper_detail_renders_summary() -> None:
    client = _client()
    response = client.get("/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49")
    assert response.status_code == 200
    assert "Structured Summary" in response.text
    assert "Citation" in response.text
    assert "@inproceedings{" in response.text
    assert "研究问题" in response.text
    assert "未来工作" in response.text


def test_ui_paper_detail_shows_manual_pdf_upload_for_unresolved_paper(
    monkeypatch,
) -> None:
    app = create_app()
    client = TestClient(app)
    monkeypatch.setattr(
        web_routes,
        "get_paper_detail_for_ui",
        lambda db, paper_id: _unresolved_paper_detail(paper_id=paper_id),
    )

    response = client.get("/ui/papers/paper-1")

    assert response.status_code == 200
    assert 'action="/ui/papers/paper-1/upload-pdf"' in response.text
    assert 'name="pdf"' in response.text
    assert "Upload PDF" in response.text


def test_ui_paper_detail_hides_manual_pdf_upload_when_pdf_exists(
    monkeypatch,
) -> None:
    app = create_app()
    client = TestClient(app)
    monkeypatch.setattr(
        web_routes,
        "get_paper_detail_for_ui",
        lambda db, paper_id: {
            **_unresolved_paper_detail(),
            "paper": {
                **_unresolved_paper_detail()["paper"],
                "best_pdf_url": "/ui/papers/paper-1/artifacts/artifact-1",
            },
        },
    )

    response = client.get("/ui/papers/paper-1")

    assert response.status_code == 200
    assert 'action="/ui/papers/paper-1/upload-pdf"' not in response.text


def test_ui_stats_and_jobs_render() -> None:
    client = _client()
    stats = client.get("/ui/stats")
    jobs = client.get("/ui/jobs")
    assert stats.status_code == 200
    assert jobs.status_code == 200
    assert "Summary Providers" in stats.text
    assert "Jobs" in jobs.text


def test_ui_admin_page_renders() -> None:
    client = _client()

    response = client.get("/ui/admin")

    assert response.status_code == 200
    assert "Admin" in response.text
    assert "Bootstrap Database" in response.text
    assert "Enqueue Resolve" in response.text
    assert "/ui/admin" in response.text


@pytest.mark.parametrize(
    ("form", "expected_message", "patch_name", "patch_return"),
    [
        (
            {"action": "bootstrap-db"},
            "Database bootstrapped.",
            "bootstrap_db_action",
            None,
        ),
        (
            {"action": "seed-icse"},
            "Seeded icse-2026 / research-track.",
            "seed_icse_action",
            {"conference_slug": "icse-2026", "track_slug": "research-track"},
        ),
        (
            {"action": "resolve", "limit": "3"},
            "Enqueued 7 resolve jobs.",
            "enqueue_resolve_action",
            7,
        ),
        (
            {"action": "parse", "limit": "4"},
            "Enqueued 8 parse jobs.",
            "enqueue_parse_action",
            8,
        ),
        (
            {"action": "summarize", "limit": "5"},
            "Enqueued 9 summarize jobs.",
            "enqueue_summarize_action",
            9,
        ),
        (
            {"action": "resummarize-fallbacks", "limit": "6"},
            "Enqueued 10 fallback re-summarize jobs.",
            "enqueue_resummarize_fallbacks_action",
            10,
        ),
        (
            {"action": "repair-resolution-status"},
            "Repaired 11 papers.",
            "repair_resolution_status_action",
            11,
        ),
        (
            {"action": "drain", "queue": "llm"},
            "Processed 12 jobs.",
            "drain_worker_action",
            12,
        ),
    ],
)
def test_ui_admin_actions_dispatch(monkeypatch, form, expected_message, patch_name, patch_return) -> None:
    client = _client()
    calls: list[object] = []

    def _record(settings, value=None):
        calls.append((settings.database_url, value))
        return patch_return

    if patch_name == "bootstrap_db_action":
        monkeypatch.setattr(web_routes, patch_name, lambda settings: calls.append(settings.database_url))
    elif patch_name == "seed_icse_action":
        monkeypatch.setattr(web_routes, patch_name, lambda settings: patch_return)
    elif patch_name == "drain_worker_action":
        monkeypatch.setattr(web_routes, patch_name, _record)
    else:
        monkeypatch.setattr(web_routes, patch_name, lambda settings, limit: calls.append((settings.database_url, limit)) or patch_return)

    response = client.post("/ui/admin", data=form)

    assert response.status_code == 200
    assert expected_message in response.text
    assert calls or patch_name == "seed_icse_action"


def test_ui_admin_action_errors_render(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(web_routes, "bootstrap_db_action", lambda settings: (_ for _ in ()).throw(RuntimeError("boom")))

    response = client.post("/ui/admin", data={"action": "bootstrap-db"})

    assert response.status_code == 400
    assert "boom" in response.text


def test_ui_manual_pdf_upload_redirects_to_paper_detail(monkeypatch) -> None:
    app = create_app()
    client = TestClient(app)
    service = _FakeUploadService()
    app.state.manual_pdf_upload_service = service
    monkeypatch.setattr(
        web_routes,
        "get_paper_detail_for_ui",
        lambda db, paper_id: _unresolved_paper_detail(paper_id=paper_id),
    )

    response = client.post(
        "/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/upload-pdf",
        files={"pdf": ("notes.pdf", _pdf_bytes(), "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == "/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49"
    )
    assert service.calls == [
        {
            "paper_id": "a7ccafea-b80f-4a01-bc18-42347badee49",
            "file_name": "notes.pdf",
            "content_type": "application/pdf",
            "content": _pdf_bytes(),
        }
    ]


def test_ui_manual_pdf_upload_uses_app_state_dependencies_by_default(monkeypatch) -> None:
    app = create_app()
    client = TestClient(app)
    repository = _FakeSaveRepository()
    storage = _FakeWriteStorage()
    app.state.job_repository = repository
    app.state.storage = storage
    monkeypatch.setattr(
        web_routes,
        "get_paper_detail_for_ui",
        lambda db, paper_id: _unresolved_paper_detail(paper_id=paper_id),
    )

    response = client.post(
        "/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/upload-pdf",
        files={"pdf": ("notes.pdf", _pdf_bytes(), "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert repository.saved_calls[0]["file_name"] == "notes.pdf"
    assert repository.enqueued[0]["job_type"] == "parse_artifact"
    assert storage.writes[0][1] == "notes.pdf"


def test_ui_manual_pdf_upload_rejects_oversized_file(monkeypatch) -> None:
    app = create_app()
    client = TestClient(app)
    monkeypatch.setattr(
        web_routes,
        "get_paper_detail_for_ui",
        lambda db, paper_id: _unresolved_paper_detail(),
    )
    original_limit = web_routes.MAX_UPLOAD_BYTES
    web_routes.MAX_UPLOAD_BYTES = 4
    try:
        response = client.post(
            "/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/upload-pdf",
            files={"pdf": ("notes.pdf", b"12345", "application/pdf")},
        )
    finally:
        web_routes.MAX_UPLOAD_BYTES = original_limit

    assert response.status_code == 400
    assert response.context["upload_error"] == "Uploaded file is too large."
    assert "Uploaded file is too large." in response.text


def test_ui_manual_pdf_upload_returns_400_with_error_context(monkeypatch) -> None:
    app = create_app()
    client = TestClient(app)
    monkeypatch.setattr(
        web_routes,
        "get_paper_detail_for_ui",
        lambda db, paper_id: _unresolved_paper_detail(paper_id=paper_id),
    )
    app.state.manual_pdf_upload_service = ManualPdfUploadService(
        repository=_FakeSaveRepository(),
        storage=_FakeWriteStorage(),
        queue=_FakeEnqueueQueue(),
    )

    response = client.post(
        "/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/upload-pdf",
        files={"pdf": ("notes.txt", b"not a pdf", "text/plain")},
    )

    assert response.status_code == 400
    assert response.template.name == "pages/paper_detail.html"
    assert response.context["upload_error"] == "Uploaded file is not a PDF."
    assert response.context["show_upload_retry"] is True
    assert str(response.context["paper"]["id"]) == "a7ccafea-b80f-4a01-bc18-42347badee49"
    assert "Uploaded file is not a PDF." in response.text
    assert 'action="/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/upload-pdf"' in response.text


def test_ui_manual_pdf_upload_rejects_resolved_paper() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/upload-pdf",
        files={"pdf": ("notes.pdf", _pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 409
    assert (
        response.context["upload_error"]
        == "Manual PDF upload is only available for unresolved papers."
    )
    assert "Manual PDF upload is only available for unresolved papers." in response.text


def test_ui_artifact_route_streams_stored_file() -> None:
    app = create_app()
    client = TestClient(app)
    app.state.job_repository = _FakeJobRepository(
        artifact=type(
            "StoredArtifact",
            (),
            {
                "id": "artifact-1",
                "storage_uri": "local://paper-1/notes.pdf",
                "mime_type": "application/pdf",
            },
        )()
    )
    storage = _FakeStorage(_pdf_bytes())
    app.state.storage = storage

    response = client.get(
        "/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/artifacts/artifact-1"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == _pdf_bytes()
    assert app.state.job_repository.calls == [
        {
            "paper_id": "a7ccafea-b80f-4a01-bc18-42347badee49",
            "artifact_id": "artifact-1",
        }
    ]
    assert storage.calls == ["local://paper-1/notes.pdf"]


def test_ui_artifact_route_returns_404_when_storage_read_fails() -> None:
    app = create_app()
    client = TestClient(app)
    app.state.job_repository = _FakeJobRepository(
        artifact=type(
            "StoredArtifact",
            (),
            {
                "id": "artifact-1",
                "storage_uri": "local://paper-1/missing.pdf",
                "mime_type": "application/pdf",
            },
        )()
    )
    app.state.storage = _MissingStorage()

    response = client.get(
        "/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/artifacts/artifact-1"
    )

    assert response.status_code == 404


def test_ui_artifact_route_returns_502_for_storage_backend_errors() -> None:
    app = create_app()
    client = TestClient(app)
    app.state.job_repository = _FakeJobRepository(
        artifact=type(
            "StoredArtifact",
            (),
            {
                "id": "artifact-1",
                "storage_uri": "s3://papers/missing.pdf",
                "mime_type": "application/pdf",
            },
        )()
    )
    app.state.storage = _BrokenStorage()

    response = client.get(
        "/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/artifacts/artifact-1"
    )

    assert response.status_code == 502
