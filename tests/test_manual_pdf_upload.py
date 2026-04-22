from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import pytest
from pypdf import PdfWriter

from research_auto.application.storage_types import StorageWriteResult
from research_auto.infrastructure.postgres.repositories import (
    PostgresJobRepository,
    PostgresPipelineRepository,
    StoredArtifactRef,
)
from research_auto.domain.records import ParsedPaper
from research_auto.interfaces.web.manual_pdf import (
    ManualPdfUploadError,
    ManualPdfUploadService,
)


def _pdf_bytes(*, trailing: bytes = b"") -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue() + trailing


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
        return SavedArtifact(
            id="artifact-1",
            storage_uri=storage_uri,
            mime_type=mime_type or "application/pdf",
        )

    def get_stored_artifact(
        self, *, paper_id: str, artifact_id: str
    ) -> StoredArtifactRef | None:
        return StoredArtifactRef(
            id=artifact_id,
            storage_uri=f"local://{paper_id}/artifact.pdf",
            mime_type="application/pdf",
        )


class FakeCursor:
    def __init__(self, fetchone_results: list[dict[str, object] | None]) -> None:
        self.fetchone_results = fetchone_results
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> None:
        self.executed.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        if not self.fetchone_results:
            return None
        return self.fetchone_results.pop(0)


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True


class FakeDb:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def connect(self) -> FakeConnection:
        return self.connection


def test_upload_pdf_stores_artifact_and_enqueues_parse() -> None:
    repository = FakeRepository()
    storage = FakeStorage()
    queue = FakeQueue()
    service = ManualPdfUploadService(
        repository=repository,
        storage=storage,
        queue=queue,
    )

    result = service.upload_pdf(
        paper_id="paper-1",
        file_name="notes.pdf",
        content_type="application/pdf",
        content=_pdf_bytes(),
    )

    assert result.artifact_id == "artifact-1"
    assert result.serving_url == "/ui/papers/paper-1/artifacts/artifact-1"
    assert storage.writes == [
        ("paper-1", "notes.pdf", _pdf_bytes(), "application/pdf")
    ]
    assert repository.saved_calls == [
        {
            "paper_id": "paper-1",
            "file_name": "notes.pdf",
            "storage_uri": "local://paper-1/notes.pdf",
            "storage_key": "paper-1/notes.pdf",
            "mime_type": "application/pdf",
            "checksum_sha256": "abc123",
            "byte_size": len(_pdf_bytes()),
        }
    ]
    assert queue.enqueued == [
        {
            "job_type": "parse_artifact",
            "payload": {
                "paper_id": "paper-1",
                "artifact_id": "artifact-1",
                "storage_uri": "local://paper-1/notes.pdf",
                "checksum_sha256": "abc123",
            },
            "dedupe_key": "parse_artifact:artifact-1:abc123",
            "priority": 40,
            "max_attempts": 5,
        }
    ]


def test_upload_pdf_rejects_non_pdf_bytes() -> None:
    service = ManualPdfUploadService(
        repository=FakeRepository(),
        storage=FakeStorage(),
        queue=FakeQueue(),
    )

    with pytest.raises(ManualPdfUploadError, match="not a PDF"):
        service.upload_pdf(
            paper_id="paper-1",
            file_name="notes.pdf",
            content_type="application/pdf",
            content=b"plain text",
        )


def test_upload_pdf_appends_pdf_extension_when_missing() -> None:
    storage = FakeStorage()
    service = ManualPdfUploadService(
        repository=FakeRepository(),
        storage=storage,
        queue=FakeQueue(),
    )

    service.upload_pdf(
        paper_id="paper-1",
        file_name="camera-ready",
        content_type="application/pdf",
        content=_pdf_bytes(),
    )

    assert storage.writes[0][1] == "camera-ready.pdf"


def test_upload_pdf_strips_browser_style_path_segments() -> None:
    storage = FakeStorage()
    service = ManualPdfUploadService(
        repository=FakeRepository(),
        storage=storage,
        queue=FakeQueue(),
    )

    service.upload_pdf(
        paper_id="paper-1",
        file_name=r"C:\fakepath\camera-ready.pdf",
        content_type="application/pdf",
        content=_pdf_bytes(),
    )

    assert storage.writes[0][1] == "camera-ready.pdf"


def test_upload_pdf_forces_pdf_mime_type() -> None:
    storage = FakeStorage()
    repository = FakeRepository()
    service = ManualPdfUploadService(
        repository=repository,
        storage=storage,
        queue=FakeQueue(),
    )

    service.upload_pdf(
        paper_id="paper-1",
        file_name="notes.pdf",
        content_type="text/plain",
        content=_pdf_bytes(),
    )

    assert storage.writes[0][3] == "application/pdf"
    assert repository.saved_calls[0]["mime_type"] == "application/pdf"


def test_upload_pdf_accepts_pdf_with_trailing_bytes() -> None:
    service = ManualPdfUploadService(
        repository=FakeRepository(),
        storage=FakeStorage(),
        queue=FakeQueue(),
    )

    result = service.upload_pdf(
        paper_id="paper-1",
        file_name="notes.pdf",
        content_type="application/pdf",
        content=_pdf_bytes(trailing=b"\ntrailer"),
    )

    assert result.serving_url == "/ui/papers/paper-1/artifacts/artifact-1"


def test_upload_pdf_rejects_spoofed_pdf_header() -> None:
    service = ManualPdfUploadService(
        repository=FakeRepository(),
        storage=FakeStorage(),
        queue=FakeQueue(),
    )

    with pytest.raises(ManualPdfUploadError, match="not a PDF"):
        service.upload_pdf(
            paper_id="paper-1",
            file_name="notes.pdf",
            content_type="application/pdf",
            content=b"%PDF-1.7\nnot really a pdf\n%%EOF",
        )


def test_save_manual_pdf_persists_artifact_and_marks_paper_resolved() -> None:
    cursor = FakeCursor(
        [
            {
                "id": "artifact-1",
                "storage_uri": "local://paper-1/notes.pdf",
                "mime_type": "application/pdf",
            }
        ]
    )
    repository = PostgresJobRepository(FakeDb(FakeConnection(cursor)))

    saved = repository.save_manual_pdf(
        paper_id="paper-1",
        file_name="notes.pdf",
        storage_uri="local://paper-1/notes.pdf",
        storage_key="paper-1/notes.pdf",
        mime_type="application/pdf",
        checksum_sha256="abc123",
        byte_size=42,
    )

    assert saved == StoredArtifactRef(
        id="artifact-1",
        storage_uri="local://paper-1/notes.pdf",
        mime_type="application/pdf",
    )
    assert len(cursor.executed) == 6
    insert_query, insert_params = cursor.executed[0]
    assert "insert into artifacts" in insert_query
    assert "on conflict (paper_id, artifact_kind, source_url) do update" in insert_query
    assert insert_params == (
        "paper-1",
        "notes.pdf",
        "manual_pdf_upload",
        "manual://paper-1/notes.pdf",
        "manual://paper-1/notes.pdf",
        "application/pdf",
        "local://paper-1/notes.pdf",
        "paper-1/notes.pdf",
        "abc123",
        42,
    )
    assert cursor.executed[1] == (
        "delete from paper_summaries where paper_id = %s",
        ("paper-1",),
    )
    assert cursor.executed[2] == (
        "delete from paper_chunks where paper_id = %s",
        ("paper-1",),
    )
    assert cursor.executed[3] == (
        "delete from paper_parses where paper_id = %s",
        ("paper-1",),
    )
    update_query, update_params = cursor.executed[4]
    assert "update papers set best_pdf_url" in update_query
    assert update_params == (
        "/ui/papers/paper-1/artifacts/artifact-1",
        "paper-1",
    )
    enqueue_query, enqueue_params = cursor.executed[5]
    assert "insert into jobs" in enqueue_query
    assert enqueue_params == (
        "parse_artifact",
        '{"paper_id": "paper-1", "artifact_id": "artifact-1", "storage_uri": "local://paper-1/notes.pdf", "checksum_sha256": "abc123"}',
        "parse_artifact:artifact-1:abc123",
        40,
        5,
    )
    assert repository.db.connection.committed is True


def test_save_manual_pdf_upserts_duplicate_manual_upload() -> None:
    cursor = FakeCursor(
        [
            {
                "id": "artifact-1",
                "storage_uri": "local://paper-1/notes.pdf",
                "mime_type": "application/pdf",
            }
        ]
    )
    repository = PostgresJobRepository(FakeDb(FakeConnection(cursor)))

    repository.save_manual_pdf(
        paper_id="paper-1",
        file_name="notes.pdf",
        storage_uri="local://paper-1/notes.pdf",
        storage_key="paper-1/notes.pdf",
        mime_type="application/pdf",
        checksum_sha256="abc123",
        byte_size=42,
    )

    insert_query, _ = cursor.executed[0]
    assert "on conflict (paper_id, artifact_kind, source_url) do update" in insert_query


def test_save_manual_pdf_clears_stale_parse_and_summary_rows() -> None:
    cursor = FakeCursor(
        [
            {
                "id": "artifact-1",
                "storage_uri": "local://paper-1/notes.pdf",
                "mime_type": "application/pdf",
            }
        ]
    )
    repository = PostgresJobRepository(FakeDb(FakeConnection(cursor)))

    repository.save_manual_pdf(
        paper_id="paper-1",
        file_name="notes.pdf",
        storage_uri="local://paper-1/notes.pdf",
        storage_key="paper-1/notes.pdf",
        mime_type="application/pdf",
        checksum_sha256="abc123",
        byte_size=42,
    )

    assert cursor.executed[1:4] == [
        ("delete from paper_summaries where paper_id = %s", ("paper-1",)),
        ("delete from paper_chunks where paper_id = %s", ("paper-1",)),
        ("delete from paper_parses where paper_id = %s", ("paper-1",)),
    ]


def test_upload_pdf_uses_checksum_in_parse_dedupe_key() -> None:
    queue = FakeQueue()
    service = ManualPdfUploadService(
        repository=FakeRepository(),
        storage=FakeStorage(),
        queue=queue,
    )

    service.upload_pdf(
        paper_id="paper-1",
        file_name="notes.pdf",
        content_type="application/pdf",
        content=_pdf_bytes(),
    )

    assert queue.enqueued[0]["dedupe_key"] == "parse_artifact:artifact-1:abc123"
    assert queue.enqueued[0]["payload"]["checksum_sha256"] == "abc123"


def test_get_stored_artifact_returns_matching_reference() -> None:
    cursor = FakeCursor(
        [
            {
                "id": "artifact-1",
                "storage_uri": "local://paper-1/notes.pdf",
                "mime_type": "application/pdf",
            }
        ]
    )
    repository = PostgresJobRepository(FakeDb(FakeConnection(cursor)))

    stored = repository.get_stored_artifact(
        paper_id="paper-1",
        artifact_id="artifact-1",
    )

    assert stored == StoredArtifactRef(
        id="artifact-1",
        storage_uri="local://paper-1/notes.pdf",
        mime_type="application/pdf",
    )
    assert cursor.executed == [
        (
            "select id, storage_uri, mime_type from artifacts where id = %s and paper_id = %s and storage_uri is not null",
            ("artifact-1", "paper-1"),
        )
    ]


def test_replace_parse_skips_stale_manual_upload_job_when_checksum_changed() -> None:
    cursor = FakeCursor(
        [
            {
                "checksum_sha256": "new-checksum",
                "resolved_url": "https://example.com/paper.pdf",
                "best_pdf_url": "https://example.com/paper.pdf",
            }
        ]
    )
    repository = PostgresPipelineRepository(FakeDb(FakeConnection(cursor)))

    repository.replace_parse(
        payload={
            "paper_id": "paper-1",
            "artifact_id": "artifact-1",
            "storage_uri": "local://paper-1/notes.pdf",
            "checksum_sha256": "old-checksum",
        },
        parsed=ParsedPaper(
            parser_version="pdf-v1",
            source_text="full text",
            full_text="full text",
            abstract_text="abstract",
            page_count=1,
            content_hash="hash",
            chunks=["chunk a"],
        ),
        prompt_version="summary-v3",
        llm_provider="github_copilot_oauth",
        llm_model="gpt-5.4-mini",
    )

    assert cursor.executed == [
        (
            "select a.checksum_sha256, a.resolved_url, p.best_pdf_url from artifacts a join papers p on p.id = a.paper_id where a.id = %s",
            ("artifact-1",),
        )
    ]


def test_replace_parse_skips_non_current_artifact_even_when_checksum_matches() -> None:
    cursor = FakeCursor(
        [
            {
                "checksum_sha256": "abc123",
                "resolved_url": "https://example.com/old.pdf",
                "best_pdf_url": "/ui/papers/paper-1/artifacts/new-artifact",
            }
        ]
    )
    repository = PostgresPipelineRepository(FakeDb(FakeConnection(cursor)))

    repository.replace_parse(
        payload={
            "paper_id": "paper-1",
            "artifact_id": "artifact-1",
            "storage_uri": "local://paper-1/notes.pdf",
            "checksum_sha256": "abc123",
        },
        parsed=ParsedPaper(
            parser_version="pdf-v1",
            source_text="full text",
            full_text="full text",
            abstract_text="abstract",
            page_count=1,
            content_hash="hash",
            chunks=["chunk a"],
        ),
        prompt_version="summary-v3",
        llm_provider="github_copilot_oauth",
        llm_model="gpt-5.4-mini",
    )

    assert cursor.executed == [
        (
            "select a.checksum_sha256, a.resolved_url, p.best_pdf_url from artifacts a join papers p on p.id = a.paper_id where a.id = %s",
            ("artifact-1",),
        )
    ]


def test_replace_resolution_preserves_existing_manual_pdf() -> None:
    cursor = FakeCursor(
        [{"best_pdf_url": "/ui/papers/paper-1/artifacts/manual-1", "has_manual_pdf": True}]
    )
    repository = PostgresPipelineRepository(FakeDb(FakeConnection(cursor)))

    repository.replace_resolution(
        paper_id="paper-1",
        result=type(
            "ResolutionResultLike",
            (),
            {
                "artifacts": [],
                "best_pdf_url": "https://example.com/auto.pdf",
                "best_landing_url": "https://example.com/landing",
                "known_doi": None,
            },
        )(),
    )

    assert cursor.executed == [
        (
            "select best_pdf_url, exists(select 1 from artifacts where paper_id = %s and artifact_kind = 'manual_pdf' and storage_uri is not null) as has_manual_pdf from papers where id = %s",
            ("paper-1", "paper-1"),
        ),
        (
            "delete from artifacts where paper_id = %s and artifact_kind <> 'manual_pdf'",
            ("paper-1",),
        ),
        (
            "update papers set best_pdf_url = %s, best_landing_url = %s, doi = coalesce(%s, doi), resolution_status = %s where id = %s",
            (
                "/ui/papers/paper-1/artifacts/manual-1",
                "https://example.com/landing",
                None,
                "resolved",
                "paper-1",
            ),
        )
    ]
