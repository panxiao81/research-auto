from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader


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
        normalized_name = _normalize_file_name(file_name)
        if not normalized_name.lower().endswith(".pdf"):
            normalized_name = f"{normalized_name}.pdf"
        if not _looks_like_pdf(content):
            raise ManualPdfUploadError("Uploaded file is not a PDF.")

        stored = self.storage.write(
            paper_id=paper_id,
            file_name=normalized_name,
            content=content,
            mime_type="application/pdf",
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
        serving_url = f"/ui/papers/{paper_id}/artifacts/{saved.id}"
        if self.queue is not self.repository:
            self.queue.enqueue(
                job_type="parse_artifact",
                payload={
                    "paper_id": paper_id,
                    "artifact_id": saved.id,
                    "storage_uri": saved.storage_uri,
                    "checksum_sha256": stored.checksum_sha256,
                },
                dedupe_key=f"parse_artifact:{saved.id}:{stored.checksum_sha256}",
                priority=40,
                max_attempts=5,
            )
        return ManualPdfUploadResult(artifact_id=saved.id, serving_url=serving_url)


def _normalize_file_name(file_name: str | None) -> str:
    raw_name = (file_name or "upload.pdf").replace("\\", "/")
    return Path(raw_name).name or "upload.pdf"


def _looks_like_pdf(content: bytes) -> bool:
    if not content.startswith(b"%PDF-"):
        return False
    if b"%%EOF" not in content:
        return False
    try:
        PdfReader(BytesIO(content))
    except Exception:  # noqa: BLE001
        return False
    return True
