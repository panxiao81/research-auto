from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg

from research_auto.config import Settings
from research_auto.crawlers.researchr import (
    checksum_text,
    crawl_track_sync,
    normalize_title,
)
from research_auto.db import Database
from research_auto.llm import PROMPT_VERSION, build_fallback_summary, build_provider
from research_auto.parsers import PARSER_VERSION, parse_pdf_file
from research_auto.resolvers import (
    apply_arxiv_fallback_reason,
    download_artifact,
    extract_doi,
    infer_arxiv_fallback_reason,
    pick_best_urls,
    resolve_detail_page,
    search_arxiv_fallback,
)


QUEUE_JOB_TYPES = {
    "all": (
        "crawl_track",
        "resolve_paper_artifacts",
        "download_artifact",
        "parse_artifact",
        "summarize_paper",
    ),
    "crawl": ("crawl_track",),
    "resolve": ("resolve_paper_artifacts",),
    "download": ("download_artifact",),
    "parse": ("parse_artifact",),
    "llm": ("summarize_paper",),
}


@dataclass(frozen=True, slots=True)
class QueuePolicy:
    name: str
    job_types: tuple[str, ...]


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
        self.provider = (
            build_provider(settings)
            if "summarize_paper" in self.queue.job_types
            else None
        )

    def run_forever(self) -> None:
        while True:
            processed = self.run_once()
            if not processed:
                time.sleep(self.settings.worker_poll_seconds)

    def drain(self) -> int:
        processed_count = 0
        while self.run_once():
            processed_count += 1
        return processed_count

    def run_once(self) -> bool:
        job = self._claim_next_job()
        if not job:
            return False

        attempt_id = self._start_attempt(job["id"])
        try:
            if job["job_type"] == "crawl_track":
                self._handle_crawl_track(job)
            elif job["job_type"] == "resolve_paper_artifacts":
                self._handle_resolve_paper_artifacts(job)
            elif job["job_type"] == "download_artifact":
                self._handle_download_artifact(job)
            elif job["job_type"] == "parse_artifact":
                self._handle_parse_artifact(job)
            elif job["job_type"] == "summarize_paper":
                self._handle_summarize_paper(job)
            else:
                raise ValueError(f"unsupported job type: {job['job_type']}")
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
            conn.commit()
        return job

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
        retry_delay_seconds = job["attempt_count"] * 30
        if job["job_type"] in {
            "summarize_paper",
            "resolve_paper_artifacts",
        } and _is_rate_limit_error(error_message):
            retry_delay_seconds = max(300, job["attempt_count"] * 300)
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

    def _handle_crawl_track(self, job: dict[str, Any]) -> None:
        payload = job["payload"]
        result, html = crawl_track_sync(
            payload["track_url"], headless=self.settings.playwright_headless
        )

        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into crawl_runs (conference_id, track_id, seed_url, status, started_at, finished_at)
                    values (%s, %s, %s, 'succeeded', now(), now())
                    returning id
                    """,
                    (
                        payload["conference_id"],
                        payload["track_id"],
                        payload["track_url"],
                    ),
                )
                crawl_run = cur.fetchone()

                cur.execute(
                    """
                    insert into page_snapshots (crawl_run_id, url, body, checksum_sha256)
                    values (%s, %s, %s, %s)
                    """,
                    (crawl_run["id"], payload["track_url"], html, checksum_text(html)),
                )

                cur.execute(
                    "delete from papers where conference_id = %s and track_id = %s",
                    (payload["conference_id"], payload["track_id"]),
                )

                for candidate in result.paper_candidates:
                    title_normalized = normalize_title(candidate.title)
                    cur.execute(
                        """
                        insert into papers (
                            conference_id,
                            track_id,
                            source_paper_key,
                            canonical_title,
                            title_normalized,
                            abstract,
                            year,
                            paper_type,
                            session_name,
                            detail_url,
                            canonical_url,
                            source_confidence,
                            status
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'discovered')
                        on conflict (conference_id, track_id, title_normalized)
                        do update set
                            canonical_title = excluded.canonical_title,
                            abstract = coalesce(excluded.abstract, papers.abstract),
                            session_name = coalesce(excluded.session_name, papers.session_name),
                            detail_url = coalesce(excluded.detail_url, papers.detail_url),
                            canonical_url = coalesce(excluded.canonical_url, papers.canonical_url),
                            source_confidence = excluded.source_confidence
                        returning id
                        """,
                        (
                            payload["conference_id"],
                            payload["track_id"],
                            candidate.detail_url,
                            candidate.title,
                            title_normalized,
                            candidate.abstract,
                            payload["year"],
                            payload.get("paper_type", "research"),
                            candidate.session_name,
                            candidate.detail_url,
                            candidate.pdf_url or candidate.detail_url,
                            0.9,
                        ),
                    )
                    paper = cur.fetchone()
                    cur.execute(
                        "delete from paper_authors where paper_id = %s", (paper["id"],)
                    )
                    for author_order, author in enumerate(candidate.authors, start=1):
                        cur.execute(
                            """
                            insert into paper_authors (paper_id, author_order, display_name)
                            values (%s, %s, %s)
                            """,
                            (paper["id"], author_order, author.name),
                        )

                    cur.execute(
                        """
                        insert into jobs (job_type, payload, dedupe_key, priority, max_attempts)
                        values (%s, %s::jsonb, %s, %s, %s)
                        on conflict do nothing
                        """,
                        (
                            "resolve_paper_artifacts",
                            json.dumps(
                                {
                                    "paper_id": str(paper["id"]),
                                    "detail_url": candidate.detail_url,
                                },
                                default=str,
                            ),
                            f"resolve_paper_artifacts:{paper['id']}",
                            20,
                            5,
                        ),
                    )
            conn.commit()

    def _handle_resolve_paper_artifacts(self, job: dict[str, Any]) -> None:
        payload = job["payload"]
        detail_url = payload.get("detail_url")

        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select
                        p.canonical_title,
                        p.doi,
                        p.detail_url,
                        p.best_pdf_url,
                        exists(select 1 from paper_parses pp where pp.paper_id = p.id) as has_parse,
                        exists(select 1 from paper_summaries ps where ps.paper_id = p.id) as has_summary
                    from papers p
                    where p.id = %s
                    """,
                    (payload["paper_id"],),
                )
                paper_row = cur.fetchone()

        if (
            paper_row["best_pdf_url"]
            and paper_row["has_parse"]
            and paper_row["has_summary"]
        ):
            return

        effective_detail_url = detail_url or paper_row.get("detail_url")
        artifacts: list[Any] = []
        detail_access_failed = False
        if effective_detail_url:
            try:
                artifacts = resolve_detail_page(effective_detail_url)
            except Exception:  # noqa: BLE001
                detail_access_failed = True
        extracted_doi = next(
            (
                extract_doi(artifact.resolved_url)
                for artifact in artifacts
                if artifact.artifact_kind == "doi"
            ),
            None,
        )
        known_doi = extracted_doi or paper_row.get("doi")
        if not pick_best_urls(artifacts)[0]:
            arxiv_artifact = search_arxiv_fallback(
                paper_row["canonical_title"], known_doi
            )
            if arxiv_artifact is not None:
                artifacts.append(
                    apply_arxiv_fallback_reason(
                        arxiv_artifact,
                        infer_arxiv_fallback_reason(
                            artifacts, detail_access_failed=detail_access_failed
                        ),
                    )
                )
        best_pdf_url, best_landing_url = pick_best_urls(artifacts)
        best_pdf_artifact = next(
            (
                artifact
                for artifact in artifacts
                if artifact.resolved_url == best_pdf_url
            ),
            None,
        )

        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from artifacts where paper_id = %s", (payload["paper_id"],)
                )
                for artifact in artifacts:
                    cur.execute(
                        """
                        insert into artifacts (
                            paper_id,
                            artifact_kind,
                            label,
                            resolution_reason,
                            source_url,
                            resolved_url,
                            mime_type,
                            downloadable,
                            download_status
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (
                            payload["paper_id"],
                            artifact.artifact_kind,
                            artifact.label,
                            artifact.resolution_reason,
                            artifact.source_url,
                            artifact.resolved_url,
                            artifact.mime_type,
                            artifact.downloadable,
                        ),
                    )

                resolution_status = "resolved" if best_pdf_url else "unresolved"
                cur.execute(
                    """
                    update papers
                    set best_pdf_url = %s,
                        best_landing_url = %s,
                        doi = coalesce(%s, doi),
                        resolution_status = %s
                    where id = %s
                    """,
                    (
                        best_pdf_url,
                        best_landing_url,
                        known_doi,
                        resolution_status,
                        payload["paper_id"],
                    ),
                )

                if best_pdf_url:
                    cur.execute(
                        """
                        insert into jobs (job_type, payload, dedupe_key, priority, max_attempts)
                        values (%s, %s::jsonb, %s, %s, %s)
                        on conflict do nothing
                        """,
                        (
                            "download_artifact",
                            json.dumps(
                                {
                                    "paper_id": payload["paper_id"],
                                    "url": best_pdf_url,
                                    "label": best_pdf_artifact.label
                                    if best_pdf_artifact
                                    else None,
                                },
                                default=str,
                            ),
                            f"download_artifact:{payload['paper_id']}:{best_pdf_url}",
                            30,
                            5,
                        ),
                    )
            conn.commit()

    def _handle_download_artifact(self, job: dict[str, Any]) -> None:
        payload = job["payload"]
        result = download_artifact(
            payload["url"],
            self.settings.artifact_root,
            payload["paper_id"],
            payload.get("label"),
        )
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update artifacts
                    set download_status = 'downloaded',
                        local_path = %s,
                        checksum_sha256 = %s,
                        byte_size = %s,
                        mime_type = coalesce(%s, mime_type),
                        downloaded_at = now()
                    where paper_id = %s and resolved_url = %s
                    """,
                    (
                        result["local_path"],
                        result["checksum_sha256"],
                        result["byte_size"],
                        result["mime_type"],
                        payload["paper_id"],
                        payload["url"],
                    ),
                )
                cur.execute(
                    "select id, mime_type, local_path from artifacts where paper_id = %s and resolved_url = %s",
                    (payload["paper_id"], payload["url"]),
                )
                artifact = cur.fetchone()
                if artifact and (
                    artifact["mime_type"] == "application/pdf"
                    or str(artifact["local_path"]).lower().endswith(".pdf")
                ):
                    cur.execute(
                        """
                        insert into jobs (job_type, payload, dedupe_key, priority, max_attempts)
                        values (%s, %s::jsonb, %s, %s, %s)
                        on conflict do nothing
                        """,
                        (
                            "parse_artifact",
                            json.dumps(
                                {
                                    "paper_id": payload["paper_id"],
                                    "artifact_id": str(artifact["id"]),
                                    "local_path": artifact["local_path"],
                                },
                                default=str,
                            ),
                            f"parse_artifact:{artifact['id']}",
                            40,
                            5,
                        ),
                    )
            conn.commit()

    def _handle_parse_artifact(self, job: dict[str, Any]) -> None:
        payload = job["payload"]
        parsed = parse_pdf_file(payload["local_path"])
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from paper_chunks where paper_parse_id in (select id from paper_parses where artifact_id = %s)",
                    (payload["artifact_id"],),
                )
                cur.execute(
                    "delete from paper_parses where artifact_id = %s",
                    (payload["artifact_id"],),
                )
                cur.execute(
                    """
                    insert into paper_parses (paper_id, artifact_id, parser_version, parse_status, full_text, abstract_text, page_count, content_hash)
                    values (%s, %s, %s, 'succeeded', %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        payload["paper_id"],
                        payload["artifact_id"],
                        PARSER_VERSION,
                        parsed.full_text,
                        parsed.abstract_text,
                        parsed.page_count,
                        parsed.content_hash,
                    ),
                )
                paper_parse = cur.fetchone()
                for index, chunk in enumerate(parsed.chunks):
                    cur.execute(
                        """
                        insert into paper_chunks (paper_parse_id, paper_id, section_name, chunk_index, token_count, content)
                        values (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            paper_parse["id"],
                            payload["paper_id"],
                            None,
                            index,
                            len(chunk.split()),
                            chunk,
                        ),
                    )
                cur.execute(
                    """
                    insert into jobs (job_type, payload, dedupe_key, priority, max_attempts)
                    values (%s, %s::jsonb, %s, %s, %s)
                    on conflict do nothing
                    """,
                    (
                        "summarize_paper",
                        json.dumps(
                            {
                                "paper_id": payload["paper_id"],
                                "paper_parse_id": str(paper_parse["id"]),
                            }
                        ),
                        f"summarize_paper:{paper_parse['id']}:{self.settings.llm_provider}:{self.settings.llm_model}:{PROMPT_VERSION}",
                        50,
                        5,
                    ),
                )
            conn.commit()

    def _handle_summarize_paper(self, job: dict[str, Any]) -> None:
        payload = job["payload"]
        provider = self.provider or build_provider(self.settings)
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select canonical_title, abstract from papers where id = %s",
                    (payload["paper_id"],),
                )
                paper = cur.fetchone()
                cur.execute(
                    "select abstract_text from paper_parses where id = %s",
                    (payload["paper_parse_id"],),
                )
                paper_parse = cur.fetchone()
                cur.execute(
                    "select content from paper_chunks where paper_parse_id = %s order by chunk_index asc",
                    (payload["paper_parse_id"],),
                )
                chunks = [row["content"] for row in cur.fetchall()]

        used_provider_name = provider.provider_name
        try:
            summary = provider.summarize(
                title=paper["canonical_title"],
                abstract=paper_parse["abstract_text"] or paper.get("abstract"),
                chunks=chunks,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limit_error(str(exc)):
                raise
            summary = build_fallback_summary(
                title=paper["canonical_title"],
                abstract=paper_parse["abstract_text"] or paper.get("abstract"),
                chunks=chunks,
                error=str(exc),
            )
            used_provider_name = f"{provider.provider_name}_fallback"

        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from paper_summaries where paper_parse_id = %s and provider = %s and model_name = %s and prompt_version = %s",
                    (
                        payload["paper_parse_id"],
                        used_provider_name,
                        self.settings.llm_model,
                        PROMPT_VERSION,
                    ),
                )
                cur.execute(
                    """
                    insert into paper_summaries (
                        paper_id,
                        paper_parse_id,
                        provider,
                        model_name,
                        prompt_version,
                        problem,
                        research_question,
                        research_question_zh,
                        method,
                        evaluation,
                        results,
                        conclusions,
                        conclusions_zh,
                        future_work,
                        future_work_zh,
                        takeaway,
                        summary_short,
                        summary_long,
                        summary_short_zh,
                        summary_long_zh,
                        contributions,
                        limitations,
                        tags,
                        raw_response
                    ) values (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
                    )
                    """,
                    (
                        payload["paper_id"],
                        payload["paper_parse_id"],
                        used_provider_name,
                        self.settings.llm_model,
                        PROMPT_VERSION,
                        summary.problem,
                        summary.research_question,
                        summary.research_question_zh,
                        summary.method,
                        summary.evaluation,
                        summary.results,
                        summary.conclusions,
                        summary.conclusions_zh,
                        summary.future_work,
                        summary.future_work_zh,
                        summary.takeaway,
                        summary.summary_short,
                        summary.summary_long,
                        summary.summary_short_zh,
                        summary.summary_long_zh,
                        json.dumps(summary.contributions),
                        json.dumps(summary.limitations),
                        json.dumps(summary.tags),
                        json.dumps(summary.raw_response),
                    ),
                )
            conn.commit()


def get_queue_policy(queue_name: str) -> QueuePolicy:
    job_types = QUEUE_JOB_TYPES.get(queue_name)
    if job_types is None:
        raise ValueError(f"unsupported worker queue: {queue_name}")
    return QueuePolicy(name=queue_name, job_types=job_types)


def _is_rate_limit_error(message: str) -> bool:
    lowered = message.lower()
    return "429" in lowered or "rate limit" in lowered or "too many requests" in lowered
