# Datalab Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Datalab the primary PDF parser while preserving the existing worker-driven parse job and keeping `pypdf` available as a fallback.

**Architecture:** Keep `ParseGateway.parse(storage_uri=...) -> ParsedPaper` as the stable seam. Extend the parsed payload to preserve backend source text, store it in `paper_parses`, and implement a Datalab-first adapter that falls back to the existing `pypdf` parser when Datalab fails or returns empty content. The worker keeps enqueueing and executing `parse_artifact`; only parser wiring and persistence change.

**Tech Stack:** Python 3.11, uv, PostgreSQL, pytest, `datalab_sdk`, `pypdf`

---

## File Map

- Modify: `pyproject.toml`
  Add the Datalab SDK dependency.
- Modify: `src/research_auto/config.py`
  Add parser backend and Datalab settings.
- Modify: `src/research_auto/application/job_executor.py`
  Persist the parser version returned by the actual parse result.
- Modify: `src/research_auto/domain/records.py`
  Extend `ParsedPaper` with generic `source_text` and `parser_version`.
- Modify: `src/research_auto/infrastructure/parsing/pdf_parser.py`
  Keep `pypdf` as the local backend and return `source_text`.
- Add: `src/research_auto/infrastructure/parsing/datalab_parser.py`
  Wrap Datalab SDK conversion and normalize its Markdown result.
- Modify: `src/research_auto/infrastructure/parsing/adapters.py`
  Build the Datalab-first parser adapter with `pypdf` fallback.
- Modify: `src/research_auto/interfaces/worker/runner.py`
  Wire parser backend settings into worker construction.
- Modify: `src/research_auto/infrastructure/postgres/schema.py`
  Add `source_text` to `paper_parses`.
- Modify: `src/research_auto/infrastructure/postgres/repositories.py`
  Persist `source_text` in `replace_parse()`.
- Modify: `tests/test_pdf_parser.py`
  Add Datalab success and fallback tests.
- Modify: `tests/test_schema.py`
  Assert `paper_parses` exposes the new `source_text` column.
- Modify: `tests/test_worker_config.py`
  Assert parser backend settings and worker wiring.

### Task 1: Extend the Parse Contract and Persistence

**Files:**
- Modify: `src/research_auto/domain/records.py`
- Modify: `src/research_auto/application/job_executor.py`
- Modify: `src/research_auto/infrastructure/postgres/schema.py`
- Modify: `src/research_auto/infrastructure/postgres/repositories.py`
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write the failing schema test for `paper_parses.source_text`**

Add this test to `tests/test_schema.py`:

```python
from research_auto.infrastructure.postgres.schema import SCHEMA_SQL


def test_schema_includes_parse_source_text() -> None:
    assert "source_text text not null" in SCHEMA_SQL
```

- [ ] **Step 2: Run the schema test to verify it fails**

Run: `uv run pytest tests/test_schema.py::test_schema_includes_parse_source_text -v`
Expected: FAIL because `source_text` is not in `paper_parses`

- [ ] **Step 3: Add `source_text` to the parse model and schema**

Update `src/research_auto/domain/records.py` to:

```python
@dataclass(slots=True)
class ParsedPaper:
    parser_version: str
    source_text: str
    full_text: str
    abstract_text: str | None
    page_count: int
    content_hash: str
    chunks: list[str]
```

Update the `paper_parses` definition in `src/research_auto/infrastructure/postgres/schema.py` to:

```sql
create table if not exists paper_parses (
    id uuid primary key default gen_random_uuid(),
    paper_id uuid not null references papers(id) on delete cascade,
    artifact_id uuid not null references artifacts(id) on delete cascade,
    parser_version text not null,
    parse_status text not null default 'succeeded',
    source_text text not null,
    full_text text not null,
    abstract_text text,
    page_count integer,
    content_hash text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (artifact_id, parser_version, content_hash)
);

alter table paper_parses add column if not exists source_text text;
update paper_parses set source_text = full_text where source_text is null;
alter table paper_parses alter column source_text set not null;
```

- [ ] **Step 4: Write the failing repository persistence test**

Add this test to `tests/test_schema.py`:

```python
def test_replace_parse_sql_persists_source_text() -> None:
    from pathlib import Path

    repo_source = Path("src/research_auto/infrastructure/postgres/repositories.py").read_text()
    assert "source_text" in repo_source
    assert "insert into paper_parses" in repo_source
```

- [ ] **Step 5: Run the two schema tests to verify the repository assertion fails**

Run: `uv run pytest tests/test_schema.py -v`
Expected: FAIL because `replace_parse()` does not insert `source_text`

- [ ] **Step 6: Persist `source_text` in `replace_parse()`**

Update the insert in `src/research_auto/infrastructure/postgres/repositories.py` to:

```python
cur.execute(
    "insert into paper_parses (paper_id, artifact_id, parser_version, parse_status, source_text, full_text, abstract_text, page_count, content_hash) values (%s, %s, %s, 'succeeded', %s, %s, %s, %s, %s) returning id",
    (
        payload["paper_id"],
        payload["artifact_id"],
        parsed.parser_version,
        parsed.source_text,
        parsed.full_text,
        parsed.abstract_text,
        parsed.page_count,
        parsed.content_hash,
    ),
)
```

Update `src/research_auto/application/job_executor.py` so `_parse_artifact()` passes the real parser version from the parse result:

```python
def _parse_artifact(self, payload: dict[str, Any]) -> None:
    parsed = self.parser.parse(storage_uri=payload["storage_uri"])
    self.repository.replace_parse(
        payload=payload,
        parsed=parsed,
        parser_version=parsed.parser_version,
        prompt_version=self.prompt_version,
        llm_provider=self.llm_provider,
        llm_model=self.llm_model,
    )
```

- [ ] **Step 7: Run the schema tests to green**

Run: `uv run pytest tests/test_schema.py -v`
Expected: PASS

- [ ] **Step 8: Commit the contract and schema work**

```bash
git add tests/test_schema.py src/research_auto/domain/records.py src/research_auto/infrastructure/postgres/schema.py src/research_auto/infrastructure/postgres/repositories.py
git add src/research_auto/application/job_executor.py
git commit -m "feat: persist parser source text"
```

### Task 2: Implement the Datalab-First Parser with `pypdf` Fallback

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/research_auto/infrastructure/parsing/pdf_parser.py`
- Add: `src/research_auto/infrastructure/parsing/datalab_parser.py`
- Modify: `src/research_auto/infrastructure/parsing/adapters.py`
- Test: `tests/test_pdf_parser.py`

- [ ] **Step 1: Write the failing Datalab success test**

Replace `tests/test_pdf_parser.py` with:

```python
from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter

from research_auto.application.storage_types import StorageWriteResult
from research_auto.infrastructure.parsing.adapters import PdfParserAdapter


def _blank_pdf_bytes() -> BytesIO:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)
    pdf_bytes.seek(0)
    return pdf_bytes


class FakeStorage:
    def __init__(self) -> None:
        self.pdf_bytes = _blank_pdf_bytes()

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
        self.pdf_bytes.seek(0)
        return self.pdf_bytes


class FakeDatalabParser:
    parser_version = "datalab-v1"

    def parse_bytes(self, *, content: bytes):
        assert content.startswith(b"%PDF")
        return {
            "parser_version": "datalab-v1",
            "source_text": "# Title\n\n## Abstract\n\nMarkdown body.",
            "full_text": "Title\n\nAbstract\n\nMarkdown body.",
            "abstract_text": "Markdown body.",
            "page_count": 1,
            "content_hash": "hash-md",
            "chunks": ["Title\n\nAbstract\n\nMarkdown body."],
        }


class FakePypdfParser:
    parser_version = "pypdf-v1"

    def parse_file(self, source):
        raise AssertionError("fallback parser should not run")


def test_pdf_parser_adapter_prefers_datalab_output() -> None:
    adapter = PdfParserAdapter(
        storage=FakeStorage(),
        primary_backend="datalab",
        datalab_parser=FakeDatalabParser(),
        pypdf_parser=FakePypdfParser(),
    )

    parsed = adapter.parse(storage_uri="local://paper-1/paper.pdf")

    assert parsed.source_text.startswith("# Title")
    assert parsed.full_text.startswith("Title")
    assert parsed.abstract_text == "Markdown body."
    assert parsed.content_hash == "hash-md"
    assert parsed.page_count == 1
```

- [ ] **Step 2: Run the success test to verify it fails**

Run: `uv run pytest tests/test_pdf_parser.py::test_pdf_parser_adapter_prefers_datalab_output -v`
Expected: FAIL because `PdfParserAdapter` does not accept backend dependencies yet

- [ ] **Step 3: Write the failing fallback test**

Add this test to `tests/test_pdf_parser.py`:

```python
class ExplodingDatalabParser:
    parser_version = "datalab-v1"

    def parse_bytes(self, *, content: bytes):
        raise RuntimeError("temporary datalab failure")


class FakePypdfParser:
    parser_version = "pypdf-v1"

    def parse_file(self, source):
        return {
            "parser_version": "pypdf-v1",
            "source_text": "plain text body",
            "full_text": "plain text body",
            "abstract_text": None,
            "page_count": 1,
            "content_hash": "hash-pdf",
            "chunks": ["plain text body"],
        }


def test_pdf_parser_adapter_falls_back_to_pypdf() -> None:
    adapter = PdfParserAdapter(
        storage=FakeStorage(),
        primary_backend="datalab",
        datalab_parser=ExplodingDatalabParser(),
        pypdf_parser=FakePypdfParser(),
    )

    parsed = adapter.parse(storage_uri="local://paper-1/paper.pdf")

    assert parsed.source_text == "plain text body"
    assert parsed.content_hash == "hash-pdf"
```

- [ ] **Step 4: Run the two parser tests to verify the fallback test also fails**

Run: `uv run pytest tests/test_pdf_parser.py -v`
Expected: FAIL because the adapter does not yet implement Datalab selection and fallback

- [ ] **Step 5: Add the Datalab SDK dependency**

Update `pyproject.toml` dependencies to include:

```toml
"datalab-sdk>=0.9.0",
```

- [ ] **Step 6: Implement the local `pypdf` backend as a focused parser module**

Refactor `src/research_auto/infrastructure/parsing/pdf_parser.py` to expose a backend class and preserve the existing normalization logic:

```python
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from typing import BinaryIO

from pypdf import PdfReader

from research_auto.domain.records import ParsedPaper


class PypdfParser:
    parser_version = "pypdf-v1"

    def parse_file(self, source: str | BinaryIO) -> dict[str, object]:
        reader = PdfReader(source)
        pages: list[str] = []
        for page in reader.pages:
            text = normalize_text(page.extract_text() or "")
            if text:
                pages.append(text)
        source_text = "\n\n".join(pages).strip()
        full_text = source_text
        return {
            "parser_version": self.parser_version,
            "source_text": source_text,
            "full_text": full_text,
            "abstract_text": extract_abstract(full_text),
            "page_count": len(reader.pages),
            "content_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "chunks": chunk_text(full_text),
        }


def parse_pdf_file(source: str | BinaryIO) -> ParsedPaper:
    parser = PypdfParser()
    return ParsedPaper(**parser.parse_file(source))
```

- [ ] **Step 7: Add the Datalab parser module**

Create `src/research_auto/infrastructure/parsing/datalab_parser.py` with:

```python
from __future__ import annotations

import hashlib
import re
import tempfile

from datalab_sdk import DatalabClient

from research_auto.infrastructure.parsing.pdf_parser import chunk_text, extract_abstract, normalize_text


class DatalabParser:
    parser_version = "datalab-v1"

    def __init__(self, *, api_key: str, base_url: str | None = None, timeout: int = 300) -> None:
        kwargs = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = DatalabClient(**kwargs)

    def parse_bytes(self, *, content: bytes) -> dict[str, object]:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(content)
            tmp.flush()
            result = self.client.convert(tmp.name)
        if not result.success or not (result.markdown or "").strip():
            raise ValueError("datalab returned empty markdown")
        source_text = result.markdown.strip()
        full_text = markdown_to_text(source_text)
        return {
            "parser_version": self.parser_version,
            "source_text": source_text,
            "full_text": full_text,
            "abstract_text": extract_abstract(full_text),
            "page_count": result.page_count or 0,
            "content_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "chunks": chunk_text(full_text),
        }


def markdown_to_text(markdown: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", markdown, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return normalize_text(text)
```

- [ ] **Step 8: Implement the backend-selecting adapter**

Replace `src/research_auto/infrastructure/parsing/adapters.py` with:

```python
from __future__ import annotations

from io import BytesIO

from research_auto.application.storage_types import ArtifactStorageGateway
from research_auto.domain.records import ParsedPaper
from research_auto.infrastructure.parsing.datalab_parser import DatalabParser
from research_auto.infrastructure.parsing.pdf_parser import PypdfParser


class PdfParserAdapter:
    def __init__(
        self,
        *,
        storage: ArtifactStorageGateway,
        primary_backend: str = "datalab",
        datalab_parser=None,
        pypdf_parser=None,
    ) -> None:
        self.storage = storage
        self.primary_backend = primary_backend
        self.datalab_parser = datalab_parser
        self.pypdf_parser = pypdf_parser or PypdfParser()

    def parse(self, *, storage_uri: str) -> ParsedPaper:
        fileobj = self.storage.read(storage_uri=storage_uri)
        content = fileobj.read()
        if self.primary_backend == "datalab" and self.datalab_parser is not None:
            try:
                return ParsedPaper(**self.datalab_parser.parse_bytes(content=content))
            except Exception:
                pass
        return ParsedPaper(**self.pypdf_parser.parse_file(BytesIO(content)))
```

- [ ] **Step 9: Run the parser tests to green**

Run: `uv run pytest tests/test_pdf_parser.py -v`
Expected: PASS

- [ ] **Step 10: Commit the parser backend work**

```bash
git add pyproject.toml tests/test_pdf_parser.py src/research_auto/infrastructure/parsing/pdf_parser.py src/research_auto/infrastructure/parsing/datalab_parser.py src/research_auto/infrastructure/parsing/adapters.py
git commit -m "feat: add datalab parser backend"
```

### Task 3: Wire Settings and Worker Construction

**Files:**
- Modify: `src/research_auto/config.py`
- Modify: `src/research_auto/interfaces/worker/runner.py`
- Test: `tests/test_worker_config.py`

- [ ] **Step 1: Write the failing settings test**

Add this test to `tests/test_worker_config.py`:

```python
def test_parser_backend_defaults_to_datalab() -> None:
    settings = _settings()

    assert settings.parser_backend == "datalab"
```

- [ ] **Step 2: Run the settings test to verify it fails**

Run: `uv run pytest tests/test_worker_config.py::test_parser_backend_defaults_to_datalab -v`
Expected: FAIL because `Settings` has no `parser_backend`

- [ ] **Step 3: Add parser settings**

Update `src/research_auto/config.py` with:

```python
    parser_backend: str = Field("datalab", alias="PARSER_BACKEND")
    datalab_api_key: str | None = Field(None, alias="DATALAB_API_KEY")
    datalab_base_url: str | None = Field(None, alias="DATALAB_BASE_URL")
    datalab_timeout_seconds: int = Field(300, alias="DATALAB_TIMEOUT_SECONDS")
```

- [ ] **Step 4: Write the failing worker wiring test**

Add this test to `tests/test_worker_config.py`:

```python
def test_worker_uses_datalab_primary_parser(monkeypatch) -> None:
    from research_auto.interfaces.worker.runner import JobWorker

    built = {}

    class FakeDatabase:
        pass

    class FakeParserAdapter:
        def __init__(self, **kwargs) -> None:
            built.update(kwargs)

    monkeypatch.setattr("research_auto.interfaces.worker.runner.PdfParserAdapter", FakeParserAdapter)
    monkeypatch.setattr("research_auto.interfaces.worker.runner.ResearchrCrawlerAdapter", lambda: object())
    monkeypatch.setattr("research_auto.interfaces.worker.runner.ResolverAdapter", lambda: object())
    monkeypatch.setattr("research_auto.interfaces.worker.runner.HttpDownloadAdapter", lambda: object())
    monkeypatch.setattr("research_auto.interfaces.worker.runner.build_storage", lambda settings: object())
    monkeypatch.setattr("research_auto.interfaces.worker.runner.PostgresJobRepository", lambda db: object())
    monkeypatch.setattr("research_auto.interfaces.worker.runner.PostgresPipelineRepository", lambda db: object())
    monkeypatch.setattr("research_auto.interfaces.worker.runner.LiteLLMSummaryAdapter", lambda settings: object())
    monkeypatch.setattr("research_auto.interfaces.worker.runner.JobExecutor", lambda **kwargs: kwargs)

    JobWorker(FakeDatabase(), _settings(PARSER_BACKEND="datalab", DATALAB_API_KEY="test-key"))

    assert built["primary_backend"] == "datalab"
```

- [ ] **Step 5: Run the worker tests to verify the wiring test fails**

Run: `uv run pytest tests/test_worker_config.py -v`
Expected: FAIL because the worker does not pass parser backend settings into `PdfParserAdapter`

- [ ] **Step 6: Wire settings into worker construction**

Update `src/research_auto/interfaces/worker/runner.py` to construct the parser like this:

```python
from research_auto.infrastructure.parsing.datalab_parser import DatalabParser


def build_parser(settings: Settings, storage: ArtifactStorageGateway) -> PdfParserAdapter:
    datalab_parser = None
    if settings.parser_backend == "datalab":
        if not settings.datalab_api_key:
            raise ValueError("DATALAB_API_KEY is required when PARSER_BACKEND=datalab")
        datalab_parser = DatalabParser(
            api_key=settings.datalab_api_key,
            base_url=settings.datalab_base_url,
            timeout=settings.datalab_timeout_seconds,
        )
    return PdfParserAdapter(
        storage=storage,
        primary_backend=settings.parser_backend,
        datalab_parser=datalab_parser,
    )
```

Then use `parser=build_parser(settings, storage)` when building `JobExecutor`.

- [ ] **Step 7: Run the worker tests to green**

Run: `uv run pytest tests/test_worker_config.py -v`
Expected: PASS

- [ ] **Step 8: Commit the worker wiring work**

```bash
git add tests/test_worker_config.py src/research_auto/config.py src/research_auto/interfaces/worker/runner.py
git commit -m "feat: configure datalab parser worker"
```

### Task 4: Final Verification

**Files:**
- Modify only if verification exposes issues in previous task files

- [ ] **Step 1: Run the focused parser-related suite**

Run: `uv run pytest tests/test_pdf_parser.py tests/test_schema.py tests/test_worker_config.py -v`
Expected: PASS

- [ ] **Step 2: Run the broader regression suite that covers current storage and worker behavior**

Run: `uv run pytest tests/test_storage_adapters.py tests/test_job_executor.py tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 4: Commit the verified integration state**

```bash
git add pyproject.toml src/research_auto/config.py src/research_auto/domain/records.py src/research_auto/infrastructure/parsing/adapters.py src/research_auto/infrastructure/parsing/datalab_parser.py src/research_auto/infrastructure/parsing/pdf_parser.py src/research_auto/infrastructure/postgres/repositories.py src/research_auto/infrastructure/postgres/schema.py src/research_auto/interfaces/worker/runner.py tests/test_pdf_parser.py tests/test_schema.py tests/test_worker_config.py
git commit -m "feat: switch pdf parsing to datalab"
```
