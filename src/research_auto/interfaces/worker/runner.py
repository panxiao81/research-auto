from __future__ import annotations

import time
import uuid

from research_auto.application.job_executor import JobExecutor
from research_auto.application.llm import PROMPT_VERSION
from research_auto.application.queue_policies import get_queue_policy
from research_auto.application.storage_types import ArtifactStorageGateway
from research_auto.config import Settings
from research_auto.infrastructure.crawlers.adapters import ResearchrCrawlerAdapter
from research_auto.infrastructure.llm.adapters import LiteLLMSummaryAdapter
from research_auto.infrastructure.parsing.adapters import PdfParserAdapter
from research_auto.infrastructure.parsing.pdf_parser import PARSER_VERSION
from research_auto.infrastructure.postgres.database import Database
from research_auto.infrastructure.postgres.repositories import (
    PostgresJobRepository,
    PostgresPipelineRepository,
)
from research_auto.infrastructure.resolution.adapters import (
    HttpDownloadAdapter,
    ResolverAdapter,
)
from research_auto.infrastructure.storage.adapters import (
    LocalArtifactStorageAdapter,
    S3ArtifactStorageAdapter,
)


class JobWorker:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        worker_id: str | None = None,
        queue_name: str | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.worker_id = worker_id or f"worker-{uuid.uuid4()}"
        self.queue = get_queue_policy(queue_name or settings.worker_queue)
        self.queue_repo = PostgresJobRepository(db)
        summarizer = (
            LiteLLMSummaryAdapter(settings)
            if "summarize_paper" in self.queue.job_types
            else None
        )
        storage = build_storage(settings)
        self.executor = JobExecutor(
            repository=PostgresPipelineRepository(db),
            queue=self.queue_repo,
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

    def run_forever(self) -> None:
        while True:
            processed = self.run_once()
            if not processed:
                time.sleep(self.settings.worker_poll_seconds)

    def drain(self) -> int:
        processed_count = 0
        while True:
            processed = self.run_once()
            if processed:
                processed_count += 1
                continue
            if self._has_pending_jobs():
                time.sleep(self.settings.worker_poll_seconds)
                continue
            break
        return processed_count

    def run_once(self) -> bool:
        job = self._claim_next_job()
        if not job:
            return False

        attempt_id = self._start_attempt(job["id"])
        try:
            self.executor.execute(job)
        except Exception as exc:  # noqa: BLE001
            self._fail_job(job, attempt_id, str(exc))
            return True

        self._succeed_job(job, attempt_id)
        return True

    def _claim_next_job(self) -> dict[str, Any] | None:
        return self.queue_repo.claim_next_job(
            queue_name=self.queue.name,
            job_types=self.queue.job_types,
            worker_id=self.worker_id,
            max_running_jobs=self.queue.max_running_jobs,
            min_start_interval_seconds=self.queue.min_start_interval_seconds,
        )

    def _has_pending_jobs(self) -> bool:
        return self.queue_repo.has_pending_jobs(job_types=self.queue.job_types)

    def _start_attempt(self, job_id: str) -> str:
        return self.queue_repo.start_job_attempt(job_id=job_id, worker_id=self.worker_id)

    def _succeed_job(self, job: dict[str, Any], attempt_id: str) -> None:
        self.queue_repo.mark_job_succeeded(job_id=job["id"], attempt_id=attempt_id)

    def _fail_job(
        self, job: dict[str, Any], attempt_id: str, error_message: str
    ) -> None:
        remaining = max(job["max_attempts"] - job["attempt_count"], 0)
        should_retry = remaining > 0
        retry_delay_seconds = self.queue.retry_delay_seconds(
            attempt_count=job["attempt_count"], error_message=error_message
        )
        self.queue_repo.mark_job_failed(
            job_id=job["id"],
            attempt_id=attempt_id,
            error_message=error_message,
            retry_delay_seconds=retry_delay_seconds,
            should_retry=should_retry,
        )


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
__all__ = ["JobWorker", "build_storage"]
