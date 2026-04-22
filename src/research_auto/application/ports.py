from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from research_auto.application.llm_types import PaperSummary
from research_auto.application.storage_types import DownloadGateway, StorageWriteResult
from research_auto.domain.records import ArtifactRecord, CrawlResult, ParsedPaper


@dataclass(frozen=True, slots=True)
class PaperResolutionContext:
    canonical_title: str
    doi: str | None
    detail_url: str | None
    best_pdf_url: str | None
    has_manual_pdf: bool
    has_parse: bool
    has_summary: bool


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    artifacts: list[ArtifactRecord]
    best_pdf_url: str | None
    best_landing_url: str | None
    known_doi: str | None
    best_pdf_label: str | None


@dataclass(frozen=True, slots=True)
class SummaryMaterial:
    canonical_title: str
    abstract: str | None
    parse_abstract: str | None
    chunks: list[str]


class CrawlGateway(Protocol):
    def crawl_track(
        self, *, track_url: str, headless: bool
    ) -> tuple[CrawlResult, str]: ...


class ResolutionGateway(Protocol):
    def resolve(
        self, *, detail_url: str | None, canonical_title: str, known_doi: str | None
    ) -> ResolutionResult: ...


class ParseGateway(Protocol):
    def parse(self, *, storage_uri: str) -> ParsedPaper: ...


class SummaryGateway(Protocol):
    provider_name: str

    def summarize(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary: ...


class PipelineRepository(Protocol):
    def replace_crawl_results(
        self, *, payload: dict[str, Any], result: CrawlResult, html: str
    ) -> None: ...

    def get_paper_resolution_context(
        self, *, paper_id: str
    ) -> PaperResolutionContext | None: ...

    def replace_resolution(
        self, *, paper_id: str, result: ResolutionResult
    ) -> None: ...

    def mark_artifact_downloaded(
        self, *, paper_id: str, url: str, result: StorageWriteResult
    ) -> dict[str, Any] | None: ...

    def replace_parse(
        self,
        *,
        payload: dict[str, Any],
        parsed: ParsedPaper,
        prompt_version: str,
        llm_provider: str,
        llm_model: str,
    ) -> None: ...

    def get_summary_material(
        self, *, paper_id: str, paper_parse_id: str
    ) -> SummaryMaterial | None: ...

    def replace_summary(
        self,
        *,
        paper_id: str,
        paper_parse_id: str,
        provider_name: str,
        model_name: str,
        prompt_version: str,
        summary: PaperSummary,
    ) -> None: ...


class JobQueue(Protocol):
    def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        dedupe_key: str,
        priority: int,
        max_attempts: int,
    ) -> None: ...
