from __future__ import annotations

from io import BytesIO

from research_auto.application.job_executor import JobExecutor
from research_auto.application.ports import (
    PaperResolutionContext,
    ResolutionResult,
    SummaryMaterial,
)
from research_auto.application.llm_types import PaperSummary
from research_auto.application.storage_types import DownloadResult, StorageWriteResult
from research_auto.domain.records import ArtifactRecord, CrawlResult, ParsedPaper


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, object]] = []

    def enqueue(self, **kwargs: object) -> None:
        self.enqueued.append(kwargs)


class FakeRepository:
    def __init__(self) -> None:
        self.resolution = PaperResolutionContext(
            canonical_title="Paper",
            doi=None,
            detail_url="https://example.com/paper",
            best_pdf_url=None,
            has_manual_pdf=False,
            has_parse=False,
            has_summary=False,
        )
        self.summary_material = SummaryMaterial(
            canonical_title="Paper",
            abstract="Abstract",
            parse_abstract=None,
            chunks=["chunk a", "chunk b"],
        )
        self.saved_resolution: ResolutionResult | None = None
        self.saved_parse: ParsedPaper | None = None
        self.saved_summary: tuple[str, PaperSummary] | None = None

    def replace_crawl_results(
        self, *, payload: dict[str, object], result: CrawlResult, html: str
    ) -> None:
        return None

    def get_paper_resolution_context(
        self, *, paper_id: str
    ) -> PaperResolutionContext | None:
        return self.resolution

    def replace_resolution(self, *, paper_id: str, result: ResolutionResult) -> None:
        self.saved_resolution = result

    def mark_artifact_downloaded(
        self, *, paper_id: str, url: str, result: object
    ) -> dict[str, object] | None:
        return None

    def replace_parse(
        self,
        *,
        payload: dict[str, object],
        parsed: ParsedPaper,
        prompt_version: str,
        llm_provider: str,
        llm_model: str,
    ) -> None:
        self.saved_parse = parsed

    def get_summary_material(
        self, *, paper_id: str, paper_parse_id: str
    ) -> SummaryMaterial | None:
        return self.summary_material

    def replace_summary(
        self,
        *,
        paper_id: str,
        paper_parse_id: str,
        provider_name: str,
        model_name: str,
        prompt_version: str,
        summary: PaperSummary,
    ) -> None:
        self.saved_summary = (provider_name, summary)


class FakeCrawler:
    def crawl_track(self, *, track_url: str, headless: bool) -> tuple[CrawlResult, str]:
        return CrawlResult(discovered=0, paper_candidates=[]), "<html></html>"


class FakeResolver:
    def resolve(
        self, *, detail_url: str | None, canonical_title: str, known_doi: str | None
    ) -> ResolutionResult:
        return ResolutionResult(
            artifacts=[
                ArtifactRecord(
                    artifact_kind="fallback_to_arxiv",
                    label="pdf",
                    resolution_reason="fallback_to_arxiv",
                    source_url=detail_url or "https://example.com",
                    resolved_url="https://arxiv.org/pdf/test.pdf",
                    downloadable=True,
                    mime_type="application/pdf",
                )
            ],
            best_pdf_url="https://arxiv.org/pdf/test.pdf",
            best_landing_url="https://arxiv.org/abs/test",
            known_doi=known_doi,
            best_pdf_label="pdf",
        )


class FakeDownloader:
    def download(self, *, url: str, paper_id: str, label: str | None) -> object:
        raise NotImplementedError


class FakeParser:
    def parse(self, *, storage_uri: str) -> ParsedPaper:
        raise NotImplementedError


class FakeSummarizer:
    provider_name = "litellm"

    def summarize(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary:
        return PaperSummary(
            problem="p",
            research_question="rq",
            research_question_zh="rq zh",
            method="m",
            evaluation="e",
            results="r",
            conclusions="c",
            conclusions_zh="c zh",
            future_work="f",
            future_work_zh="f zh",
            takeaway="t",
            summary_short="short",
            summary_long="long",
            summary_short_zh="short zh",
            summary_long_zh="long zh",
            contributions=[],
            limitations=[],
            tags=[],
            raw_response={},
        )


def _executor(repo: FakeRepository, queue: FakeQueue) -> JobExecutor:
    return JobExecutor(
        repository=repo,
        queue=queue,
        crawler=FakeCrawler(),
        resolver=FakeResolver(),
        downloader=FakeDownloader(),
        storage=object(),
        parser=FakeParser(),
        summarizer=FakeSummarizer(),
        playwright_headless=True,
        prompt_version="summary-v3",
        llm_provider="github_copilot_oauth",
        llm_model="gpt-5.4-mini",
    )


def test_job_executor_enqueues_download_after_resolution() -> None:
    repo = FakeRepository()
    queue = FakeQueue()

    _executor(repo, queue).execute(
        {"job_type": "resolve_paper_artifacts", "payload": {"paper_id": "paper-1"}}
    )

    assert repo.saved_resolution is not None
    assert queue.enqueued[0]["job_type"] == "download_artifact"


def test_job_executor_refreshes_resolution_but_skips_download_when_manual_pdf_exists() -> None:
    repo = FakeRepository()
    repo.resolution = PaperResolutionContext(
        canonical_title="Paper",
        doi=None,
        detail_url="https://example.com/paper",
        best_pdf_url="/ui/papers/paper-1/artifacts/artifact-1",
        has_manual_pdf=True,
        has_parse=False,
        has_summary=False,
    )
    queue = FakeQueue()

    _executor(repo, queue).execute(
        {"job_type": "resolve_paper_artifacts", "payload": {"paper_id": "paper-1"}}
    )

    assert repo.saved_resolution is not None
    assert queue.enqueued == []


def test_job_executor_saves_summary_via_port() -> None:
    repo = FakeRepository()
    queue = FakeQueue()

    _executor(repo, queue).execute(
        {
            "job_type": "summarize_paper",
            "payload": {"paper_id": "paper-1", "paper_parse_id": "parse-1"},
        }
    )

    assert repo.saved_summary is not None
    assert repo.saved_summary[0] == "litellm"


def test_job_executor_saves_parse_via_port_without_separate_parser_version() -> None:
    repo = FakeRepository()
    queue = FakeQueue()

    class Parser:
        def parse(self, *, storage_uri: str) -> ParsedPaper:
            assert storage_uri == "local://paper-1/paper.pdf"
            return ParsedPaper(
                parser_version="pdf-v2",
                source_text="raw parser output",
                full_text="cleaned parser output",
                abstract_text="abstract",
                page_count=12,
                content_hash="hash-1",
                chunks=["chunk one", "chunk two"],
            )

    executor = JobExecutor(
        repository=repo,
        queue=queue,
        crawler=FakeCrawler(),
        resolver=FakeResolver(),
        downloader=FakeDownloader(),
        storage=object(),
        parser=Parser(),
        summarizer=FakeSummarizer(),
        playwright_headless=True,
        prompt_version="summary-v3",
        llm_provider="github_copilot_oauth",
        llm_model="gpt-5.4-mini",
    )

    executor.execute(
        {
            "job_type": "parse_artifact",
            "payload": {
                "paper_id": "paper-1",
                "artifact_id": "artifact-1",
                "storage_uri": "local://paper-1/paper.pdf",
            },
        }
    )

    assert repo.saved_parse is not None
    assert repo.saved_parse.parser_version == "pdf-v2"


def test_job_executor_downloads_writes_and_queues_parse() -> None:
    queue = FakeQueue()

    class Repo(FakeRepository):
        def mark_artifact_downloaded(
            self, *, paper_id: str, url: str, result: object
        ) -> dict[str, object] | None:
            return {"id": "artifact-1", "mime_type": "application/pdf"}

    class Downloader:
        def download(
            self, *, url: str, paper_id: str, label: str | None
        ) -> DownloadResult:
            return DownloadResult(
                content=b"%PDF-1.4",
                file_name="paper.pdf",
                checksum_sha256="abc",
                byte_size=8,
                mime_type="application/pdf",
            )

    class Storage:
        def __init__(self) -> None:
            self.writes: list[tuple[str, str, bytes]] = []

        def write(
            self,
            *,
            paper_id: str,
            file_name: str,
            content: bytes,
            mime_type: str | None,
        ) -> StorageWriteResult:
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

    storage = Storage()
    executor = JobExecutor(
        repository=Repo(),
        queue=queue,
        crawler=FakeCrawler(),
        resolver=FakeResolver(),
        downloader=Downloader(),
        storage=storage,
        parser=FakeParser(),
        summarizer=FakeSummarizer(),
        playwright_headless=True,
        prompt_version="summary-v3",
        llm_provider="github_copilot_oauth",
        llm_model="gpt-5.4-mini",
    )

    executor.execute(
        {
            "job_type": "download_artifact",
            "payload": {"paper_id": "paper-1", "url": "https://example.com/paper.pdf"},
        }
    )

    assert storage.writes == [("paper-1", "paper.pdf", b"%PDF-1.4")]
    assert queue.enqueued[0]["job_type"] == "parse_artifact"
    assert queue.enqueued[0]["payload"]["storage_uri"] == "local://paper-1/paper.pdf"
    assert queue.enqueued[0]["payload"]["checksum_sha256"] == "abc"
    assert queue.enqueued[0]["dedupe_key"] == "parse_artifact:artifact-1:abc"


def test_job_executor_queues_parse_for_pdf_filename_with_generic_mime() -> None:
    queue = FakeQueue()

    class Repo(FakeRepository):
        def mark_artifact_downloaded(
            self, *, paper_id: str, url: str, result: object
        ) -> dict[str, object] | None:
            return {"id": "artifact-1", "mime_type": "application/octet-stream"}

    class Downloader:
        def download(
            self, *, url: str, paper_id: str, label: str | None
        ) -> DownloadResult:
            return DownloadResult(
                content=b"%PDF-1.4",
                file_name="paper.pdf",
                checksum_sha256="abc",
                byte_size=8,
                mime_type="application/octet-stream",
            )

    class Storage:
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

        def read(self, *, storage_uri: str):
            return BytesIO(b"%PDF-1.4")

    executor = JobExecutor(
        repository=Repo(),
        queue=queue,
        crawler=FakeCrawler(),
        resolver=FakeResolver(),
        downloader=Downloader(),
        storage=Storage(),
        parser=FakeParser(),
        summarizer=FakeSummarizer(),
        playwright_headless=True,
        prompt_version="summary-v3",
        llm_provider="github_copilot_oauth",
        llm_model="gpt-5.4-mini",
    )

    executor.execute(
        {
            "job_type": "download_artifact",
            "payload": {"paper_id": "paper-1", "url": "https://example.com/paper.pdf"},
        }
    )

    assert queue.enqueued[0]["job_type"] == "parse_artifact"
    assert queue.enqueued[0]["dedupe_key"] == "parse_artifact:artifact-1:abc"
