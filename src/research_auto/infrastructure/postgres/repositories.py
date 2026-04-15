from __future__ import annotations

import json
from typing import Any

from research_auto.application.ports import (
    DownloadResult,
    PaperResolutionContext,
    ResolutionResult,
    SummaryMaterial,
)
from research_auto.application.query_services import Page
from research_auto.domain.records import CrawlResult, ParsedPaper
from research_auto.infrastructure.crawlers.researchr import (
    checksum_text,
    normalize_title,
)
from research_auto.infrastructure.postgres.database import Database
from research_auto.application.llm_types import PaperSummary


class PostgresCatalogRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_conference(
        self, *, slug: str, name: str, year: int, homepage_url: str, source_system: str
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into conferences (slug, name, year, homepage_url, source_system)
                    values (%s, %s, %s, %s, %s)
                    on conflict (slug) do update
                    set name = excluded.name,
                        year = excluded.year,
                        homepage_url = excluded.homepage_url,
                        source_system = excluded.source_system
                    returning *
                    """,
                    (slug, name, year, homepage_url, source_system),
                )
                row = cur.fetchone()
            conn.commit()
        return row

    def upsert_track(
        self, *, conference_id: str, slug: str, name: str, track_url: str
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into tracks (conference_id, slug, name, track_url)
                    values (%s, %s, %s, %s)
                    on conflict (conference_id, slug) do update
                    set name = excluded.name,
                        track_url = excluded.track_url
                    returning *
                    """,
                    (conference_id, slug, name, track_url),
                )
                row = cur.fetchone()
            conn.commit()
        return row


class PostgresJobRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
        priority: int = 100,
        max_attempts: int = 5,
    ) -> bool:
        return self.enqueue_job(
            job_type=job_type,
            payload=payload,
            dedupe_key=dedupe_key,
            priority=priority,
            max_attempts=max_attempts,
        )

    def enqueue_job(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
        priority: int = 100,
        max_attempts: int = 5,
    ) -> bool:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into jobs (job_type, payload, dedupe_key, priority, max_attempts)
                    values (%s, %s::jsonb, %s, %s, %s)
                    on conflict do nothing
                    """,
                    (
                        job_type,
                        json.dumps(payload, default=str),
                        dedupe_key,
                        priority,
                        max_attempts,
                    ),
                )
                inserted = cur.rowcount > 0
            conn.commit()
        return inserted

    def fetch_all(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return list(cur.fetchall())

    def fetch_one(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchone()


class PostgresReadRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.jobs = PostgresJobRepository(db)

    def list_papers(
        self,
        *,
        page: int,
        page_size: int,
        q: str | None,
        resolved: bool | None,
        has_pdf: bool | None,
        parsed: bool | None,
        summarized: bool | None,
        provider: str | None,
        sort: str,
        order: str,
    ) -> Page:
        filters: list[str] = []
        params: list[Any] = []
        if q:
            like_q = f"%{q}%"
            filters.append(
                "(p.canonical_title ilike %s or coalesce(p.abstract, '') ilike %s or coalesce(ls.summary_short, '') ilike %s or coalesce(ls.summary_short_zh, '') ilike %s)"
            )
            params.extend([like_q, like_q, like_q, like_q])
        if resolved is not None:
            filters.append("(p.resolution_status = 'resolved') = %s")
            params.append(resolved)
        if has_pdf is not None:
            filters.append("(p.best_pdf_url is not null) = %s")
            params.append(has_pdf)
        if parsed is not None:
            filters.append("(lp.id is not null) = %s")
            params.append(parsed)
        if summarized is not None:
            filters.append("(ls.id is not null) = %s")
            params.append(summarized)
        if provider:
            filters.append("coalesce(ls.provider, '') = %s")
            params.append(provider)
        where_sql = f"where {' and '.join(filters)}" if filters else ""
        order_sql = build_paper_order_sql(sort, order)
        offset = max(page - 1, 0) * page_size
        base_from = f"""
            from papers p
            join conferences c on c.id = p.conference_id
            left join tracks t on t.id = p.track_id
            left join lateral (select pp.* from paper_parses pp where pp.paper_id = p.id order by pp.created_at desc limit 1) lp on true
            left join lateral (select ps.* from paper_summaries ps where ps.paper_id = p.id order by ps.created_at desc limit 1) ls on true
            {where_sql}
        """
        total = self.jobs.fetch_one(
            f"select count(*) as count {base_from}", tuple(params)
        )["count"]
        rows = self.jobs.fetch_all(
            f"""
            select p.id, p.canonical_title, p.year, p.session_name, p.best_pdf_url, p.resolution_status, p.updated_at,
                   c.slug as conference_slug, t.name as track_name, lp.id as latest_parse_id, lp.page_count,
                   ls.id as latest_summary_id, ls.provider, ls.model_name, ls.prompt_version, ls.summary_short,
                   ls.summary_short_zh, ls.research_question_zh,
                   (p.best_pdf_url is not null) as has_pdf,
                   (lp.id is not null) as is_parsed,
                   (ls.id is not null) as is_summarized,
                   (coalesce(ls.summary_short_zh, '') <> '') as has_chinese_summary,
                   (coalesce(ls.provider, '') like '%%fallback') as is_fallback_summary
            {base_from}
            order by {order_sql}
            limit %s offset %s
            """,
            tuple([*params, page_size, offset]),
        )
        return Page(items=rows, total=total, page=page, page_size=page_size)

    def get_paper_detail(self, *, paper_id: str) -> dict[str, Any]:
        paper = self.jobs.fetch_one(
            """
            select p.*, c.name as conference_name, c.slug as conference_slug, t.name as track_name
            from papers p join conferences c on c.id = p.conference_id left join tracks t on t.id = p.track_id
            where p.id = %s
            """,
            (paper_id,),
        )
        authors = self.jobs.fetch_all(
            "select author_order, display_name, affiliation from paper_authors where paper_id = %s order by author_order asc",
            (paper_id,),
        )
        artifacts = self.jobs.fetch_all(
            "select artifact_kind, label, resolution_reason, resolved_url, download_status, local_path from artifacts where paper_id = %s order by created_at asc",
            (paper_id,),
        )
        parse = self.jobs.fetch_one(
            "select * from paper_parses where paper_id = %s order by created_at desc limit 1",
            (paper_id,),
        )
        chunks = self.jobs.fetch_all(
            "select chunk_index, token_count, left(content, 1200) as content from paper_chunks where paper_id = %s order by chunk_index asc limit 12",
            (paper_id,),
        )
        summary = self.jobs.fetch_one(
            "select * from paper_summaries where paper_id = %s order by created_at desc limit 1",
            (paper_id,),
        )
        return {
            "paper": paper,
            "authors": authors,
            "artifacts": artifacts,
            "parse": parse,
            "chunks": chunks,
            "summary": summary,
        }

    def search_papers(self, *, q: str, limit: int) -> list[dict[str, Any]]:
        like_q = f"%{q}%"
        return self.jobs.fetch_all(
            """
            select distinct on (p.id)
                p.id, p.canonical_title, p.best_pdf_url, p.resolution_status,
                ls.summary_short, ls.summary_short_zh, ls.research_question, ls.research_question_zh,
                ts_rank_cd(
                    setweight(to_tsvector('english', coalesce(p.canonical_title, '')), 'A') ||
                    setweight(to_tsvector('english', coalesce(p.abstract, '')), 'B') ||
                    setweight(to_tsvector('english', coalesce(ls.summary_short, '')), 'B') ||
                    setweight(to_tsvector('simple', coalesce(ls.summary_short_zh, '')), 'B') ||
                    setweight(to_tsvector('english', coalesce(lp.full_text, '')), 'C'),
                    plainto_tsquery('english', %s)
                ) as rank
            from papers p
            left join lateral (select * from paper_summaries ps where ps.paper_id = p.id order by ps.created_at desc limit 1) ls on true
            left join lateral (select * from paper_parses pp where pp.paper_id = p.id order by pp.created_at desc limit 1) lp on true
            where (
                setweight(to_tsvector('english', coalesce(p.canonical_title, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(p.abstract, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(ls.summary_short, '')), 'B') ||
                setweight(to_tsvector('simple', coalesce(ls.summary_short_zh, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(lp.full_text, '')), 'C')
            ) @@ plainto_tsquery('english', %s)
            or p.canonical_title ilike %s
            or coalesce(ls.summary_short, '') ilike %s
            or coalesce(ls.summary_short_zh, '') ilike %s
            order by p.id, rank desc nulls last, ls.created_at desc nulls last
            limit %s
            """,
            (q, q, like_q, like_q, like_q, limit),
        )

    def get_stats(self) -> dict[str, Any]:
        counts = {
            "papers_total": "select count(*) as count from papers",
            "papers_resolved": "select count(*) as count from papers where resolution_status = 'resolved'",
            "papers_unresolved": "select count(*) as count from papers where resolution_status = 'unresolved'",
            "papers_with_pdf": "select count(*) as count from papers where best_pdf_url is not null",
            "resolved_without_pdf": "select count(*) as count from papers where resolution_status = 'resolved' and best_pdf_url is null",
            "paper_parses": "select count(*) as count from paper_parses",
            "paper_summaries": "select count(*) as count from paper_summaries",
            "jobs_failed": "select count(*) as count from jobs where status = 'failed'",
        }
        result = {
            label: self.jobs.fetch_one(query, ())["count"]
            for label, query in counts.items()
        }
        result["summary_providers"] = self.jobs.fetch_all(
            "select provider, count(*) as count from paper_summaries group by provider order by count desc"
        )
        result["artifact_kinds"] = self.jobs.fetch_all(
            "select artifact_kind, count(*) as count from artifacts group by artifact_kind order by count desc"
        )
        result["failed_job_types"] = self.jobs.fetch_all(
            "select job_type, count(*) as count from jobs where status = 'failed' group by job_type order by count desc"
        )
        result["fallback_reasons"] = self.jobs.fetch_all(
            "select coalesce(a.resolution_reason, 'no_reason_recorded') as reason, count(*) as count from artifacts a where a.artifact_kind = 'fallback_to_arxiv' group by coalesce(a.resolution_reason, 'no_reason_recorded') order by count desc"
        )
        return result

    def list_jobs(
        self, *, status: str | None, job_type: str | None, limit: int
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if status:
            filters.append("status = %s")
            params.append(status)
        if job_type:
            filters.append("job_type = %s")
            params.append(job_type)
        where_sql = f"where {' and '.join(filters)}" if filters else ""
        return self.jobs.fetch_all(
            f"select id, job_type, status, priority, attempt_count, max_attempts, last_error, updated_at from jobs {where_sql} order by updated_at desc limit %s",
            tuple([*params, limit]),
        )

    def list_conferences(self) -> list[dict[str, Any]]:
        return self.jobs.fetch_all(
            "select * from conferences order by year desc, name asc"
        )

    def list_tracks(self) -> list[dict[str, Any]]:
        return self.jobs.fetch_all(
            "select t.*, c.slug as conference_slug from tracks t join conferences c on c.id = t.conference_id order by c.year desc, t.name asc"
        )

    def list_api_papers(self, *, limit: int) -> list[dict[str, Any]]:
        return self.jobs.fetch_all(
            "select p.*, c.slug as conference_slug, t.slug as track_slug from papers p join conferences c on c.id = p.conference_id left join tracks t on t.id = p.track_id order by p.created_at desc limit %s",
            (limit,),
        )

    def get_api_paper(self, *, paper_id: str) -> dict[str, Any]:
        paper = self.jobs.fetch_one("select * from papers where id = %s", (paper_id,))
        authors = self.jobs.fetch_all(
            "select author_order, display_name, affiliation, orcid from paper_authors where paper_id = %s order by author_order asc",
            (paper_id,),
        )
        parses = self.jobs.fetch_all(
            "select id, parser_version, parse_status, page_count, created_at from paper_parses where paper_id = %s order by created_at desc",
            (paper_id,),
        )
        summaries = self.jobs.fetch_all(
            "select provider, model_name, prompt_version, summary_short, tags, created_at from paper_summaries where paper_id = %s order by created_at desc",
            (paper_id,),
        )
        return {
            "paper": paper,
            "authors": authors,
            "parses": parses,
            "summaries": summaries,
        }

    def get_paper_question_context(
        self, *, paper_id: str, question: str, limit: int
    ) -> tuple[list[str], dict[str, Any] | None]:
        rows = self.jobs.fetch_all(
            "select content from paper_chunks where paper_id = %s order by ts_rank_cd(to_tsvector('english', coalesce(content, '')), plainto_tsquery('english', %s)) desc, chunk_index asc limit %s",
            (paper_id, question, limit),
        )
        summary = self.jobs.fetch_one(
            "select * from paper_summaries where paper_id = %s order by created_at desc limit 1",
            (paper_id,),
        )
        return [row["content"] for row in rows], summary

    def get_library_question_context(
        self, *, question: str, limit: int
    ) -> tuple[list[str], dict[str, Any] | None, list[dict[str, Any]]]:
        rows = self.jobs.fetch_all(
            "select p.id as paper_id, p.canonical_title, pc.content from paper_chunks pc join papers p on p.id = pc.paper_id order by ts_rank_cd(to_tsvector('english', coalesce(pc.content, '')), plainto_tsquery('english', %s)) desc, pc.created_at asc limit %s",
            (question, limit),
        )
        context_chunks = [
            f"[{row['canonical_title']}] {row['content']}" for row in rows
        ]
        summary = (
            self.jobs.fetch_one(
                "select * from paper_summaries where paper_id = %s order by created_at desc limit 1",
                (rows[0]["paper_id"],),
            )
            if rows
            else None
        )
        return context_chunks, summary, rows


class PostgresPipelineRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.jobs = PostgresJobRepository(db)

    def replace_crawl_results(
        self, *, payload: dict[str, Any], result: CrawlResult, html: str
    ) -> None:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into crawl_runs (conference_id, track_id, seed_url, status, started_at, finished_at) values (%s, %s, %s, 'succeeded', now(), now()) returning id",
                    (
                        payload["conference_id"],
                        payload["track_id"],
                        payload["track_url"],
                    ),
                )
                crawl_run = cur.fetchone()
                cur.execute(
                    "insert into page_snapshots (crawl_run_id, url, body, checksum_sha256) values (%s, %s, %s, %s)",
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
                        insert into papers (conference_id, track_id, source_paper_key, canonical_title, title_normalized, abstract, year, paper_type, session_name, detail_url, canonical_url, source_confidence, status)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'discovered')
                        on conflict (conference_id, track_id, title_normalized)
                        do update set canonical_title = excluded.canonical_title,
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
                        "insert into jobs (job_type, payload, dedupe_key, priority, max_attempts) values (%s, %s::jsonb, %s, %s, %s) on conflict do nothing",
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
        row = self.jobs.fetch_one(
            "select p.canonical_title, p.doi, p.detail_url, p.best_pdf_url, exists(select 1 from paper_parses pp where pp.paper_id = p.id) as has_parse, exists(select 1 from paper_summaries ps where ps.paper_id = p.id) as has_summary from papers p where p.id = %s",
            (paper_id,),
        )
        return PaperResolutionContext(**row) if row else None

    def replace_resolution(self, *, paper_id: str, result: ResolutionResult) -> None:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from artifacts where paper_id = %s", (paper_id,))
                for artifact in result.artifacts:
                    cur.execute(
                        "insert into artifacts (paper_id, artifact_kind, label, resolution_reason, source_url, resolved_url, mime_type, downloadable, download_status) values (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')",
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
                    "update papers set best_pdf_url = %s, best_landing_url = %s, doi = coalesce(%s, doi), resolution_status = %s where id = %s",
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
                    "update artifacts set download_status = 'downloaded', local_path = %s, checksum_sha256 = %s, byte_size = %s, mime_type = coalesce(%s, mime_type), downloaded_at = now() where paper_id = %s and resolved_url = %s",
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
                    "insert into paper_parses (paper_id, artifact_id, parser_version, parse_status, full_text, abstract_text, page_count, content_hash) values (%s, %s, %s, 'succeeded', %s, %s, %s, %s) returning id",
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
                    "insert into jobs (job_type, payload, dedupe_key, priority, max_attempts) values (%s, %s::jsonb, %s, %s, %s) on conflict do nothing",
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
        if not paper or not paper_parse:
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
                    insert into paper_summaries (paper_id, paper_parse_id, provider, model_name, prompt_version, problem, research_question, research_question_zh, method, evaluation, results, conclusions, conclusions_zh, future_work, future_work_zh, takeaway, summary_short, summary_long, summary_short_zh, summary_long_zh, contributions, limitations, tags, raw_response)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
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


def build_paper_order_sql(sort: str, order: str) -> str:
    direction = "desc" if order.lower() == "desc" else "asc"
    if sort == "title":
        return f"p.canonical_title {direction}, p.updated_at desc"
    if sort == "year":
        return f"p.year {direction}, p.updated_at desc"
    if sort == "updated":
        return f"p.updated_at {direction}"
    return "(ls.id is not null) desc, p.updated_at desc"
