from __future__ import annotations

import json
from typing import Any

from research_auto.adapters.sql import PostgresSqlQueries
from research_auto.application.ports import (
    DownloadResult,
    PaperResolutionContext,
    ResolutionResult,
    SummaryMaterial,
)
from research_auto.crawlers.researchr import checksum_text, normalize_title
from research_auto.db import Database
from research_auto.llm import PaperSummary
from research_auto.models import CrawlResult
from research_auto.parsers import ParsedPaper


class PostgresPipelineRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.queries = PostgresSqlQueries(db)

    def replace_crawl_results(
        self, *, payload: dict[str, Any], result: CrawlResult, html: str
    ) -> None:
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
                            conference_id, track_id, source_paper_key, canonical_title, title_normalized, abstract,
                            year, paper_type, session_name, detail_url, canonical_url, source_confidence, status
                        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'discovered')
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
                            "insert into paper_authors (paper_id, author_order, display_name) values (%s, %s, %s)",
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

    def get_paper_resolution_context(
        self, *, paper_id: str
    ) -> PaperResolutionContext | None:
        row = self.queries.get_row(
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
            (paper_id,),
        )
        if row is None:
            return None
        return PaperResolutionContext(**row)

    def replace_resolution(self, *, paper_id: str, result: ResolutionResult) -> None:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from artifacts where paper_id = %s", (paper_id,))
                for artifact in result.artifacts:
                    cur.execute(
                        """
                        insert into artifacts (
                            paper_id, artifact_kind, label, resolution_reason, source_url, resolved_url, mime_type, downloadable, download_status
                        ) values (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (
                            paper_id,
                            artifact.artifact_kind,
                            artifact.label,
                            artifact.resolution_reason,
                            artifact.source_url,
                            artifact.resolved_url,
                            artifact.mime_type,
                            artifact.downloadable,
                        ),
                    )
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
                        result.best_pdf_url,
                        result.best_landing_url,
                        result.known_doi,
                        "resolved" if result.best_pdf_url else "unresolved",
                        paper_id,
                    ),
                )
            conn.commit()

    def mark_artifact_downloaded(
        self, *, paper_id: str, url: str, result: DownloadResult
    ) -> dict[str, Any] | None:
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
                        result.local_path,
                        result.checksum_sha256,
                        result.byte_size,
                        result.mime_type,
                        paper_id,
                        url,
                    ),
                )
                cur.execute(
                    "select id, mime_type, local_path from artifacts where paper_id = %s and resolved_url = %s",
                    (paper_id, url),
                )
                artifact = cur.fetchone()
            conn.commit()
        return artifact

    def replace_parse(
        self,
        *,
        payload: dict[str, Any],
        parsed: ParsedPaper,
        parser_version: str,
        prompt_version: str,
        llm_provider: str,
        llm_model: str,
    ) -> None:
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
                        parser_version,
                        parsed.full_text,
                        parsed.abstract_text,
                        parsed.page_count,
                        parsed.content_hash,
                    ),
                )
                paper_parse = cur.fetchone()
                for index, chunk in enumerate(parsed.chunks):
                    cur.execute(
                        "insert into paper_chunks (paper_parse_id, paper_id, section_name, chunk_index, token_count, content) values (%s, %s, %s, %s, %s, %s)",
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
                        f"summarize_paper:{paper_parse['id']}:{llm_provider}:{llm_model}:{prompt_version}",
                        50,
                        5,
                    ),
                )
            conn.commit()

    def get_summary_material(
        self, *, paper_id: str, paper_parse_id: str
    ) -> SummaryMaterial | None:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select canonical_title, abstract from papers where id = %s",
                    (paper_id,),
                )
                paper = cur.fetchone()
                cur.execute(
                    "select abstract_text from paper_parses where id = %s",
                    (paper_parse_id,),
                )
                paper_parse = cur.fetchone()
                cur.execute(
                    "select content from paper_chunks where paper_parse_id = %s order by chunk_index asc",
                    (paper_parse_id,),
                )
                chunks = [row["content"] for row in cur.fetchall()]
        if paper is None or paper_parse is None:
            return None
        return SummaryMaterial(
            canonical_title=paper["canonical_title"],
            abstract=paper.get("abstract"),
            parse_abstract=paper_parse.get("abstract_text"),
            chunks=chunks,
        )

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
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from paper_summaries where paper_parse_id = %s and provider = %s and model_name = %s and prompt_version = %s",
                    (paper_parse_id, provider_name, model_name, prompt_version),
                )
                cur.execute(
                    """
                    insert into paper_summaries (
                        paper_id, paper_parse_id, provider, model_name, prompt_version, problem, research_question,
                        research_question_zh, method, evaluation, results, conclusions, conclusions_zh,
                        future_work, future_work_zh, takeaway, summary_short, summary_long, summary_short_zh,
                        summary_long_zh, contributions, limitations, tags, raw_response
                    ) values (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
                    )
                    """,
                    (
                        paper_id,
                        paper_parse_id,
                        provider_name,
                        model_name,
                        prompt_version,
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
