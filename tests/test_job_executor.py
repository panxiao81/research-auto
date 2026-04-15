from __future__ import annotations

from dataclasses import dataclass

from research_auto.application.job_executor import JobExecutor
from research_auto.application.ports import (
    PaperResolutionContext,
    ResolutionResult,
    SummaryMaterial,
)
from research_auto.llm import PaperSummary
from research_auto.models import CrawlResult
from research_auto.parsers import ParsedPaper
from research_auto.resolvers import ArtifactRecord


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
        parser_version: str,
        prompt_version: str,
        llm_provider: str,
        llm_model: str,
    ) -> None:
        return None

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
    def download(
        self, *, url: str, artifact_root: str, paper_id: str, label: str | None
    ) -> object:
        raise NotImplementedError


class FakeParser:
    def parse(self, *, local_path: str) -> ParsedPaper:
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
        parser=FakeParser(),
        summarizer=FakeSummarizer(),
        playwright_headless=True,
        artifact_root="data/artifacts",
        parser_version="pdf-v1",
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
