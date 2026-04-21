# Configurable Artifact Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** add a configurable artifact storage layer with local and S3 backends, both exposing `write` and `read`, so PDF parsing always uses the storage `read` interface.

**Architecture:** downloading a paper fetches bytes from the remote URL, then the storage backend persists those bytes and returns a storage URI. Local and S3 backends implement the same storage protocol with `write` and `read` methods. The parser resolves PDFs by asking the storage backend to `read(storage_uri)`, so parsing is backend-agnostic and no longer depends on filesystem paths.

**Tech Stack:** Python 3.11, `pypdf`, `boto3`, `pytest`, existing `uv` project layout.

---

### Task 1: Add an artifact storage port

**Files:**
- Modify `src/research_auto/application/ports.py:29-65`
- Create `src/research_auto/application/storage_types.py`
- Test: `tests/test_storage_contracts.py` (new)

- [ ] **Step 1: Write the failing test**

```python
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
        def write(self, *, paper_id: str, file_name: str, content: bytes, mime_type: str | None) -> StorageWriteResult:
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
    result = storage.write(paper_id="paper-1", file_name="paper.pdf", content=b"%PDF-1.4", mime_type="application/pdf")

    assert result.storage_uri == "local://paper-1/paper.pdf"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_storage_contracts.py -v`
Expected: fail because `StorageWriteResult` and `ArtifactStorageGateway` do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
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
    ) -> StorageWriteResult:
        raise NotImplementedError

    def read(self, *, storage_uri: str) -> BytesIO:
        raise NotImplementedError


class DownloadGateway(Protocol):
    def download(self, *, url: str, paper_id: str, label: str | None) -> DownloadResult:
        raise NotImplementedError
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest -q tests/test_storage_contracts.py -v`
Expected: pass.

---

### Task 2: Make PDF parsing read from storage

**Files:**
- Modify `src/research_auto/infrastructure/parsing/pdf_parser.py:14-33`
- Modify `src/research_auto/infrastructure/parsing/adapters.py:7-9`
- Test: `tests/test_pdf_parser.py` (new)

- [ ] **Step 1: Write the failing test**

```python
from io import BytesIO
from pypdf import PdfWriter
from research_auto.application.storage_types import StorageWriteResult

def test_pdf_parser_adapter_reads_from_storage() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)
    pdf_bytes.seek(0)

    class FakeStorage:
        def write(self, *, paper_id: str, file_name: str, content: bytes, mime_type: str | None) -> StorageWriteResult:
            return StorageWriteResult(
                storage_uri=f"local://{paper_id}/{file_name}",
                storage_key=f"{paper_id}/{file_name}",
                byte_size=len(content),
                mime_type=mime_type,
                checksum_sha256="abc",
            )

        def read(self, *, storage_uri: str) -> BytesIO:
            assert storage_uri == "local://paper-1/paper.pdf"
            pdf_bytes.seek(0)
            return pdf_bytes

    adapter = PdfParserAdapter(storage=FakeStorage())
    parsed = adapter.parse(storage_uri="local://paper-1/paper.pdf")

    assert parsed.page_count == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_pdf_parser.py::test_pdf_parser_adapter_reads_from_storage -v`
Expected: fail because the parser adapter does not accept `storage_uri` yet.

- [ ] **Step 3: Write the minimal implementation**

```python
from io import BytesIO
from typing import BinaryIO

def parse_pdf_file(source: str | BinaryIO) -> ParsedPaper:
    reader = PdfReader(source)
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = normalize_text(text)
        if text:
            pages.append(text)
    full_text = "\n\n".join(pages).strip()
    content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    abstract = extract_abstract(full_text)
    chunks = chunk_text(full_text)
    return ParsedPaper(
        full_text=full_text,
        abstract_text=abstract,
        page_count=len(reader.pages),
        content_hash=content_hash,
        chunks=chunks,
    )


class PdfParserAdapter:
    def __init__(self, *, storage: ArtifactStorageGateway) -> None:
        self.storage = storage

    def parse(self, *, storage_uri: str) -> ParsedPaper:
        return parse_pdf_file(self.storage.read(storage_uri=storage_uri))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest -q tests/test_pdf_parser.py::test_pdf_parser_adapter_reads_from_storage -v`
Expected: pass.

---

### Task 3: Implement local and S3 storage backends

**Files:**
- Create `src/research_auto/infrastructure/storage/adapters.py`
- Modify `src/research_auto/config.py:6-30`
- Modify `pyproject.toml:10-22`
- Modify `tests/test_worker_config.py:1-60`
- Test: `tests/test_storage_adapters.py` (new)

- [ ] **Step 1: Write the failing test**

```python
from io import BytesIO

def test_local_storage_round_trip(tmp_path) -> None:
    storage = LocalArtifactStorageAdapter(artifact_root=str(tmp_path))
    written = storage.write(
        paper_id="paper-1",
        file_name="paper.pdf",
        content=b"%PDF-1.4",
        mime_type="application/pdf",
    )

    assert written.storage_uri == f"local://paper-1/paper.pdf"
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
    monkeypatch.setattr("research_auto.infrastructure.storage.adapters.boto3.client", lambda *args, **kwargs: fake_s3)

    storage = S3ArtifactStorageAdapter(bucket="papers", prefix="artifacts")
    written = storage.write(
        paper_id="paper-1",
        file_name="paper.pdf",
        content=b"%PDF-1.4",
        mime_type="application/pdf",
    )

    assert written.storage_uri == "s3://papers/artifacts/paper-1/paper.pdf"
    assert storage.read(storage_uri=written.storage_uri).read() == b"%PDF-1.4"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_storage_adapters.py -v`
Expected: fail because the storage adapters do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
import hashlib
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import boto3

from research_auto.application.storage_types import ArtifactStorageGateway, StorageWriteResult


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
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        response = self.s3.get_object(Bucket=bucket, Key=key)
        return BytesIO(response["Body"].read())
```

Add `storage_backend` to settings:

```python
class Settings(BaseSettings):
    storage_backend: str = Field("local", alias="STORAGE_BACKEND")
    artifact_root: str = Field("data/artifacts", alias="ARTIFACT_ROOT")
    s3_bucket: str | None = Field(None, alias="S3_BUCKET")
    s3_prefix: str = Field("papers", alias="S3_PREFIX")
    s3_region: str | None = Field(None, alias="AWS_REGION")
    s3_endpoint_url: str | None = Field(None, alias="AWS_ENDPOINT_URL")
```

Add `boto3` to `pyproject.toml`.

Extend `tests/test_worker_config.py` so it verifies `STORAGE_BACKEND=local|s3` and the S3 prefix default:

```python
def test_storage_backend_defaults_and_overrides() -> None:
    settings = _settings(STORAGE_BACKEND="s3", S3_BUCKET="papers")

    assert settings.storage_backend == "s3"
    assert settings.s3_prefix == "papers"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest -q tests/test_storage_adapters.py tests/test_worker_config.py -v`
Expected: pass.

---

### Task 4: Wire storage into download, resolve, and parse jobs

**Files:**
- Modify `src/research_auto/application/job_executor.py:18-168`
- Modify `src/research_auto/interfaces/worker/runner.py:15-57`
- Modify `src/research_auto/infrastructure/resolution/adapters.py:1-76` (rename the current filesystem-specific downloader into `HttpDownloadAdapter`)
- Modify `src/research_auto/infrastructure/resolution/service.py:752-775`
- Modify `src/research_auto/infrastructure/postgres/repositories.py:521-543`
- Modify `src/research_auto/infrastructure/postgres/schema.py:134-152`
- Modify `tests/test_job_executor.py:1-201`

- [ ] **Step 1: Write the failing test**

```python
from io import BytesIO

from research_auto.application.storage_types import DownloadResult, StorageWriteResult

def test_job_executor_downloads_writes_and_queues_parse() -> None:
    repo = FakeRepository()
    queue = FakeQueue()

    class FakeDownloader:
        def download(self, *, url: str, paper_id: str, label: str | None) -> DownloadResult:
            return DownloadResult(
                content=b"%PDF-1.4",
                file_name="paper.pdf",
                checksum_sha256="abc",
                byte_size=8,
                mime_type="application/pdf",
            )

    class FakeStorage:
        def __init__(self) -> None:
            self.writes: list[tuple[str, str, bytes]] = []

        def write(self, *, paper_id: str, file_name: str, content: bytes, mime_type: str | None) -> StorageWriteResult:
            self.writes.append((paper_id, file_name, content))
            return StorageWriteResult(
                storage_uri=f"local://{paper_id}/{file_name}",
                storage_key=f"{paper_id}/{file_name}",
                byte_size=len(content),
                mime_type=mime_type,
                checksum_sha256="abc",
            )

        def read(self, *, storage_uri: str):
            return BytesIO(b"%PDF-1.4")

    executor = JobExecutor(
        repository=repo,
        queue=queue,
        crawler=FakeCrawler(),
        resolver=FakeResolver(),
        downloader=FakeDownloader(),
        storage=FakeStorage(),
        parser=FakeParser(),
        summarizer=FakeSummarizer(),
        playwright_headless=True,
        parser_version="pdf-v1",
        prompt_version="summary-v3",
        llm_provider="github_copilot_oauth",
        llm_model="gpt-5.4-mini",
    )

    executor.execute({"job_type": "download_artifact", "payload": {"paper_id": "paper-1", "url": "https://example.com/paper.pdf"}})

    assert queue.enqueued[0]["job_type"] == "parse_artifact"
    assert queue.enqueued[0]["payload"]["storage_uri"] == "local://paper-1/paper.pdf"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_job_executor.py::test_job_executor_downloads_writes_and_queues_parse -v`
Expected: fail because `JobExecutor` does not yet accept storage or queue parse by `storage_uri`.

- [ ] **Step 3: Write the minimal implementation**

```python
def _download_artifact(self, payload: dict[str, Any]) -> None:
    downloaded = self.downloader.download(
        url=payload["url"],
        paper_id=payload["paper_id"],
        label=payload.get("label"),
    )
    stored = self.storage.write(
        paper_id=payload["paper_id"],
        file_name=downloaded.file_name,
        content=downloaded.content,
        mime_type=downloaded.mime_type,
    )
    artifact = self.repository.mark_artifact_downloaded(
        paper_id=payload["paper_id"], url=payload["url"], result=stored
    )
    if artifact is None:
        return
    self.queue.enqueue(
        job_type="parse_artifact",
        payload={"paper_id": payload["paper_id"], "artifact_id": str(artifact["id"]), "storage_uri": stored.storage_uri},
        dedupe_key=f"parse_artifact:{artifact['id']}",
        priority=40,
        max_attempts=5,
    )
```

Update `src/research_auto/infrastructure/resolution/service.py` so the fetch helper returns raw bytes and metadata:

```python
import hashlib
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

def download_artifact(url: str, label: str | None) -> dict[str, Any]:
    file_name = safe_file_name(label or Path(urlparse(url).path).name or "artifact.bin")
    request = Request(url, headers={"User-Agent": get_user_agent()})
    with urlopen(request, timeout=120) as response:
        payload = response.read()
        content_type = response.headers.get_content_type()

    return {
        "content": payload,
        "checksum_sha256": hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
        "mime_type": content_type,
        "file_name": file_name,
    }
```

Update `src/research_auto/infrastructure/resolution/adapters.py` so the HTTP download adapter wraps that result in `DownloadResult`:

```python
from research_auto.application.storage_types import DownloadResult

class HttpDownloadAdapter:
    def download(self, *, url: str, paper_id: str, label: str | None) -> DownloadResult:
        result = download_artifact(url, label)
        return DownloadResult(
            content=result["content"],
            file_name=result["file_name"],
            checksum_sha256=result["checksum_sha256"],
            byte_size=result["byte_size"],
            mime_type=result["mime_type"],
        )
```

Update `PdfParserAdapter.parse()` callers so parse jobs pass `storage_uri` instead of `local_path`.

Update `mark_artifact_downloaded()` to persist `storage_uri` and `storage_key` in the artifacts table.

Add a worker bootstrap helper that selects the storage backend from config:

```python
def build_storage(settings: Settings) -> ArtifactStorageGateway:
    if settings.storage_backend == "local":
        return LocalArtifactStorageAdapter(artifact_root=settings.artifact_root)
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        return S3ArtifactStorageAdapter(
            bucket=settings.s3_bucket,
            prefix=settings.s3_prefix,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
        )
    raise ValueError(f"unsupported storage backend: {settings.storage_backend}")


storage = build_storage(settings)
executor = JobExecutor(
    repository=PostgresPipelineRepository(db),
    queue=PostgresJobRepository(db),
    crawler=ResearchrCrawlerAdapter(),
    resolver=ResolverAdapter(),
    downloader=HttpDownloadAdapter(),
    storage=storage,
    parser=PdfParserAdapter(storage=storage),
    summarizer=summarizer,
    playwright_headless=settings.playwright_headless,
    parser_version=PARSER_VERSION,
    prompt_version=PROMPT_VERSION,
    llm_provider=settings.llm_provider,
    llm_model=settings.llm_model,
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest -q tests/test_job_executor.py -v`
Expected: pass.

---

### Task 5: End-to-end verification

**Files:**
- No new files

- [ ] **Step 1: Run the focused test set**

Run: `uv run pytest -q tests/test_storage_contracts.py tests/test_pdf_parser.py tests/test_storage_adapters.py tests/test_job_executor.py tests/test_worker_config.py`

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`

- [ ] **Step 3: Fix any regressions introduced by the API change**

If any consumer still expects `local_path`, update it to use `storage_uri` and `ArtifactStorageGateway.read` instead.
