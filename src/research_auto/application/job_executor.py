from __future__ import annotations

from typing import Any

from research_auto.application.ports import (
    CrawlGateway,
    DownloadGateway,
    JobQueue,
    ParseGateway,
    PipelineRepository,
    ResolutionGateway,
    SummaryGateway,
)
from research_auto.application.llm import build_fallback_summary
from research_auto.application.llm_types import PaperSummary
from research_auto.application.storage_types import ArtifactStorageGateway


class JobExecutor:
    def __init__(
        self,
        *,
        repository: PipelineRepository,
        queue: JobQueue,
        crawler: CrawlGateway,
        resolver: ResolutionGateway,
        downloader: DownloadGateway,
        storage: ArtifactStorageGateway,
        parser: ParseGateway,
        summarizer: SummaryGateway | None,
        playwright_headless: bool,
        prompt_version: str,
        llm_provider: str,
        llm_model: str,
    ) -> None:
        self.repository = repository
        self.queue = queue
        self.crawler = crawler
        self.resolver = resolver
        self.downloader = downloader
        self.storage = storage
        self.parser = parser
        self.summarizer = summarizer
        self.playwright_headless = playwright_headless
        self.prompt_version = prompt_version
        self.llm_provider = llm_provider
        self.llm_model = llm_model

    def execute(self, job: dict[str, Any]) -> None:
        handlers = {
            "crawl_track": self._crawl_track,
            "resolve_paper_artifacts": self._resolve_paper_artifacts,
            "download_artifact": self._download_artifact,
            "parse_artifact": self._parse_artifact,
            "summarize_paper": self._summarize_paper,
        }
        handler = handlers.get(job["job_type"])
        if handler is None:
            raise ValueError(f"unsupported job type: {job['job_type']}")
        handler(job["payload"])

    def _crawl_track(self, payload: dict[str, Any]) -> None:
        result, html = self.crawler.crawl_track(
            track_url=payload["track_url"], headless=self.playwright_headless
        )
        self.repository.replace_crawl_results(payload=payload, result=result, html=html)

    def _resolve_paper_artifacts(self, payload: dict[str, Any]) -> None:
        paper = self.repository.get_paper_resolution_context(
            paper_id=payload["paper_id"]
        )
        if paper is None:
            return
        if paper.best_pdf_url and paper.has_parse and paper.has_summary:
            return

        result = self.resolver.resolve(
            detail_url=payload.get("detail_url") or paper.detail_url,
            canonical_title=paper.canonical_title,
            known_doi=paper.doi,
        )
        self.repository.replace_resolution(paper_id=payload["paper_id"], result=result)
        if result.best_pdf_url:
            self.queue.enqueue(
                job_type="download_artifact",
                payload={
                    "paper_id": payload["paper_id"],
                    "url": result.best_pdf_url,
                    "label": result.best_pdf_label,
                },
                dedupe_key=f"download_artifact:{payload['paper_id']}:{result.best_pdf_url}",
                priority=30,
                max_attempts=5,
            )

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
        if artifact and (
            artifact["mime_type"] == "application/pdf"
            or stored.storage_key.lower().endswith(".pdf")
        ):
            self.queue.enqueue(
                job_type="parse_artifact",
                payload={
                    "paper_id": payload["paper_id"],
                    "artifact_id": str(artifact["id"]),
                    "storage_uri": stored.storage_uri,
                },
                dedupe_key=f"parse_artifact:{artifact['id']}",
                priority=40,
                max_attempts=5,
            )

    def _parse_artifact(self, payload: dict[str, Any]) -> None:
        parsed = self.parser.parse(storage_uri=payload["storage_uri"])
        self.repository.replace_parse(
            payload=payload,
            parsed=parsed,
            prompt_version=self.prompt_version,
            llm_provider=self.llm_provider,
            llm_model=self.llm_model,
        )

    def _summarize_paper(self, payload: dict[str, Any]) -> None:
        if self.summarizer is None:
            raise ValueError("summarizer is not configured")
        material = self.repository.get_summary_material(
            paper_id=payload["paper_id"], paper_parse_id=payload["paper_parse_id"]
        )
        if material is None:
            return

        used_provider_name = self.summarizer.provider_name
        try:
            summary = self.summarizer.summarize(
                title=material.canonical_title,
                abstract=material.parse_abstract or material.abstract,
                chunks=material.chunks,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limit_error(str(exc)):
                raise
            summary = build_fallback_summary(
                title=material.canonical_title,
                abstract=material.parse_abstract or material.abstract,
                chunks=material.chunks,
                error=str(exc),
            )
            used_provider_name = f"{self.summarizer.provider_name}_fallback"

        self.repository.replace_summary(
            paper_id=payload["paper_id"],
            paper_parse_id=payload["paper_parse_id"],
            provider_name=used_provider_name,
            model_name=self.llm_model,
            prompt_version=self.prompt_version,
            summary=summary,
        )


def _is_rate_limit_error(message: str) -> bool:
    lowered = message.lower()
    return "429" in lowered or "rate limit" in lowered or "too many requests" in lowered
