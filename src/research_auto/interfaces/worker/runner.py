from __future__ import annotations

import time
import uuid
from typing import Any

from research_auto.infrastructure.crawlers.adapters import ResearchrCrawlerAdapter
from research_auto.infrastructure.parsing.adapters import PdfParserAdapter
from research_auto.infrastructure.postgres.database import Database
from research_auto.infrastructure.postgres.repositories import (
    PostgresJobRepository,
    PostgresPipelineRepository,
)
from research_auto.infrastructure.llm.adapters import LiteLLMSummaryAdapter
from research_auto.infrastructure.resolution.adapters import (
    FilesystemDownloadAdapter,
    ResolverAdapter,
)
from research_auto.application.job_executor import JobExecutor
from research_auto.application.queue_policies import get_queue_policy
from research_auto.config import Settings
from research_auto.application.llm import PROMPT_VERSION
from research_auto.infrastructure.parsing.pdf_parser import PARSER_VERSION


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
        summarizer = (
            LiteLLMSummaryAdapter(settings)
            if "summarize_paper" in self.queue.job_types
            else None
        )
        self.executor = JobExecutor(
            repository=PostgresPipelineRepository(db),
            queue=PostgresJobRepository(db),
            crawler=ResearchrCrawlerAdapter(),
            resolver=ResolverAdapter(),
            downloader=FilesystemDownloadAdapter(),
            parser=PdfParserAdapter(),
            summarizer=summarizer,
            playwright_headless=settings.playwright_headless,
            artifact_root=settings.artifact_root,
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
        try:
            job = self._claim_next_job()
        except _QueueNotReady:
            return False
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
        if not self.queue.job_types:
            return None
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                self._ensure_queue_can_start(cur)
                cur.execute(
                    """
                    with candidate as (
                        select id
                        from jobs
                        where status = 'pending'
                          and available_at <= now()
                          and job_type = any(%s)
                        order by priority asc, created_at asc
                        limit 1
                        for update skip locked
                    )
                    update jobs j
                    set status = 'running',
                        locked_at = now(),
                        worker_id = %s,
                        attempt_count = attempt_count + 1
                    from candidate
                    where j.id = candidate.id
                    returning j.*
                    """,
                    (list(self.queue.job_types), self.worker_id),
                )
                job = cur.fetchone()
                if job is not None:
                    cur.execute(
                        """
                        insert into worker_queue_state (queue_name, last_started_at)
                        values (%s, now())
                        on conflict (queue_name) do update
                        set last_started_at = excluded.last_started_at
                        """,
                        (self.queue.name,),
                    )
            conn.commit()
        return job

    def _has_pending_jobs(self) -> bool:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select exists(select 1 from jobs where status = 'pending' and job_type = any(%s)) as has_pending",
                    (list(self.queue.job_types),),
                )
                row = cur.fetchone()
        return bool(row["has_pending"])

    def _ensure_queue_can_start(self, cur: Any) -> None:
        cur.execute(
            """
            insert into worker_queue_state (queue_name)
            values (%s)
            on conflict (queue_name) do nothing
            """,
            (self.queue.name,),
        )
        cur.execute(
            "select queue_name, last_started_at from worker_queue_state where queue_name = %s for update",
            (self.queue.name,),
        )
        state = cur.fetchone()

        if self.queue.max_running_jobs is not None:
            cur.execute(
                "select count(*) as count from jobs where status = 'running' and job_type = any(%s)",
                (list(self.queue.job_types),),
            )
            if cur.fetchone()["count"] >= self.queue.max_running_jobs:
                raise _QueueNotReady

        if self.queue.min_start_interval_seconds > 0:
            cur.execute(
                "select now() >= coalesce(%s, '-infinity'::timestamptz) + (%s * interval '1 second') as can_start",
                (
                    state["last_started_at"] if state else None,
                    self.queue.min_start_interval_seconds,
                ),
            )
            if not cur.fetchone()["can_start"]:
                raise _QueueNotReady

    def _start_attempt(self, job_id: str) -> str:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into job_attempts (job_id, worker_id)
                    values (%s, %s)
                    returning id
                    """,
                    (job_id, self.worker_id),
                )
                row = cur.fetchone()
            conn.commit()
        return row["id"]

    def _succeed_job(self, job: dict[str, Any], attempt_id: str) -> None:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update jobs set status = 'succeeded', locked_at = null, worker_id = null where id = %s",
                    (job["id"],),
                )
                cur.execute(
                    "update job_attempts set finished_at = now(), success = true where id = %s",
                    (attempt_id,),
                )
            conn.commit()

    def _fail_job(
        self, job: dict[str, Any], attempt_id: str, error_message: str
    ) -> None:
        remaining = max(job["max_attempts"] - job["attempt_count"], 0)
        should_retry = remaining > 0
        retry_delay_seconds = self.queue.retry_delay_seconds(
            attempt_count=job["attempt_count"], error_message=error_message
        )
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                if should_retry:
                    cur.execute(
                        """
                        update jobs
                        set status = 'pending',
                            available_at = now() + (%s * interval '1 second'),
                            locked_at = null,
                            worker_id = null,
                            last_error = %s
                        where id = %s
                        """,
                        (retry_delay_seconds, error_message, job["id"]),
                    )
                else:
                    cur.execute(
                        """
                        update jobs
                        set status = 'failed',
                            locked_at = null,
                            worker_id = null,
                            last_error = %s
                        where id = %s
                        """,
                        (error_message, job["id"]),
                    )
                cur.execute(
                    """
                    update job_attempts
                    set finished_at = now(), success = false, error_message = %s
                    where id = %s
                    """,
                    (error_message, attempt_id),
                )
            conn.commit()


class _QueueNotReady(RuntimeError):
    pass


__all__ = ["JobWorker"]
