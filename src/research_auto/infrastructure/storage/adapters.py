from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import boto3

from research_auto.application.storage_types import StorageWriteResult


class LocalArtifactStorageAdapter:
    def __init__(self, *, artifact_root: str) -> None:
        self.artifact_root = Path(artifact_root)

    def write(
        self,
        *,
        paper_id: str,
        file_name: str,
        content: bytes,
        mime_type: str | None,
    ) -> StorageWriteResult:
        target_dir = self.artifact_root / paper_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / file_name
        target_path.write_bytes(content)
        return StorageWriteResult(
            storage_uri=f"local://{paper_id}/{file_name}",
            storage_key=f"{paper_id}/{file_name}",
            byte_size=len(content),
            mime_type=mime_type,
            checksum_sha256=hashlib.sha256(content).hexdigest(),
        )

    def read(self, *, storage_uri: str) -> BytesIO:
        parsed = urlparse(storage_uri)
        if parsed.scheme != "local":
            raise ValueError(f"unsupported storage uri: {storage_uri}")
        path = self.artifact_root / parsed.netloc / parsed.path.lstrip("/")
        return BytesIO(path.read_bytes())


class S3ArtifactStorageAdapter:
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        region: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.s3 = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)

    def write(
        self,
        *,
        paper_id: str,
        file_name: str,
        content: bytes,
        mime_type: str | None,
    ) -> StorageWriteResult:
        key = f"{self.prefix}/{paper_id}/{file_name}"
        self.s3.upload_fileobj(BytesIO(content), self.bucket, key)
        return StorageWriteResult(
            storage_uri=f"s3://{self.bucket}/{key}",
            storage_key=key,
            byte_size=len(content),
            mime_type=mime_type,
            checksum_sha256=hashlib.sha256(content).hexdigest(),
        )

    def read(self, *, storage_uri: str) -> BytesIO:
        parsed = urlparse(storage_uri)
        if parsed.scheme != "s3":
            raise ValueError(f"unsupported storage uri: {storage_uri}")
        response = self.s3.get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))
        return BytesIO(response["Body"].read())
