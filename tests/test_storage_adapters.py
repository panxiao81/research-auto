from __future__ import annotations

from io import BytesIO

from research_auto.infrastructure.storage.adapters import (
    LocalArtifactStorageAdapter,
    S3ArtifactStorageAdapter,
)


def test_local_storage_round_trip(tmp_path) -> None:
    storage = LocalArtifactStorageAdapter(artifact_root=str(tmp_path))
    written = storage.write(
        paper_id="paper-1",
        file_name="paper.pdf",
        content=b"%PDF-1.4",
        mime_type="application/pdf",
    )

    assert written.storage_uri == "local://paper-1/paper.pdf"
    assert storage.read(storage_uri=written.storage_uri).read() == b"%PDF-1.4"


def test_s3_storage_upload_and_read(monkeypatch) -> None:
    class FakeS3:
        def __init__(self) -> None:
            self.objects: dict[tuple[str, str], bytes] = {}

        def upload_fileobj(self, fileobj, bucket, key):
            self.objects[(bucket, key)] = fileobj.read()

        def get_object(self, Bucket, Key):
            return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    fake_s3 = FakeS3()
    monkeypatch.setattr(
        "research_auto.infrastructure.storage.adapters.boto3.client",
        lambda *args, **kwargs: fake_s3,
    )

    storage = S3ArtifactStorageAdapter(bucket="papers", prefix="artifacts")
    written = storage.write(
        paper_id="paper-1",
        file_name="paper.pdf",
        content=b"%PDF-1.4",
        mime_type="application/pdf",
    )

    assert written.storage_uri == "s3://papers/artifacts/paper-1/paper.pdf"
    assert storage.read(storage_uri=written.storage_uri).read() == b"%PDF-1.4"


def test_local_storage_read_raises_for_missing_file(tmp_path) -> None:
    storage = LocalArtifactStorageAdapter(artifact_root=str(tmp_path))

    try:
        storage.read(storage_uri="local://paper-1/missing.pdf")
    except FileNotFoundError:
        pass
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected FileNotFoundError")


def test_s3_storage_read_rejects_non_s3_uri(monkeypatch) -> None:
    class FakeS3:
        def get_object(self, Bucket, Key):  # pragma: no cover - should not be reached
            raise AssertionError("unexpected get_object call")

    monkeypatch.setattr(
        "research_auto.infrastructure.storage.adapters.boto3.client",
        lambda *args, **kwargs: FakeS3(),
    )

    storage = S3ArtifactStorageAdapter(bucket="papers", prefix="artifacts")

    try:
        storage.read(storage_uri="local://paper-1/paper.pdf")
    except ValueError as exc:
        assert str(exc) == "unsupported storage uri: local://paper-1/paper.pdf"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected ValueError")
