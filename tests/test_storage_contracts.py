from __future__ import annotations

from io import BytesIO

from research_auto.application.storage_types import ArtifactStorageGateway, StorageWriteResult


def test_storage_result_has_uri_and_key() -> None:
    result = StorageWriteResult(
        storage_uri="s3://papers/artifacts/paper-1/paper.pdf",
        storage_key="artifacts/paper-1/paper.pdf",
        byte_size=8,
        mime_type="application/pdf",
        checksum_sha256="abc",
    )

    assert result.storage_uri.endswith("paper.pdf")
    assert result.storage_key == "artifacts/paper-1/paper.pdf"


def test_storage_gateway_protocol_shape() -> None:
    class FakeStorage:
        def write(
            self,
            *,
            paper_id: str,
            file_name: str,
            content: bytes,
            mime_type: str | None,
        ) -> StorageWriteResult:
            return StorageWriteResult(
                storage_uri=f"local://{paper_id}/{file_name}",
                storage_key=f"{paper_id}/{file_name}",
                byte_size=len(content),
                mime_type=mime_type,
                checksum_sha256="abc",
            )

        def read(self, *, storage_uri: str) -> BytesIO:
            return BytesIO(b"%PDF-1.4")

    storage: ArtifactStorageGateway = FakeStorage()
    result = storage.write(
        paper_id="paper-1",
        file_name="paper.pdf",
        content=b"%PDF-1.4",
        mime_type="application/pdf",
    )

    assert result.storage_uri == "local://paper-1/paper.pdf"
