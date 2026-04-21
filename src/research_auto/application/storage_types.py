from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DownloadResult:
    content: bytes
    file_name: str
    checksum_sha256: str
    byte_size: int
    mime_type: str | None


@dataclass(frozen=True, slots=True)
class StorageWriteResult:
    storage_uri: str
    storage_key: str
    byte_size: int
    mime_type: str | None
    checksum_sha256: str


class ArtifactStorageGateway(Protocol):
    def write(
        self,
        *,
        paper_id: str,
        file_name: str,
        content: bytes,
        mime_type: str | None,
    ) -> StorageWriteResult: ...

    def read(self, *, storage_uri: str) -> BytesIO: ...


class DownloadGateway(Protocol):
    def download(self, *, url: str, paper_id: str, label: str | None) -> DownloadResult: ...
