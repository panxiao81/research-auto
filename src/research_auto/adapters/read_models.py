from __future__ import annotations

from typing import Any

from research_auto.adapters.sql import PostgresSqlQueries
from research_auto.application.query_services import Page
from research_auto.db import Database


class PostgresReadRepository:
    def __init__(self, db: Database) -> None:
        self.queries = PostgresSqlQueries(db)

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
            left join lateral (
                select pp.* from paper_parses pp where pp.paper_id = p.id order by pp.created_at desc limit 1
            ) lp on true
            left join lateral (
                select ps.* from paper_summaries ps where ps.paper_id = p.id order by ps.created_at desc limit 1
            ) ls on true
            {where_sql}
        """
        total = self.queries.get_row(
            f"select count(*) as count {base_from}", tuple(params)
        )["count"]
        rows = self.queries.list_rows(
            f"""
            select
                p.id, p.canonical_title, p.year, p.session_name, p.best_pdf_url, p.resolution_status, p.updated_at,
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
        paper = self.queries.get_row(
            """
            select p.*, c.name as conference_name, c.slug as conference_slug, t.name as track_name
            from papers p
            join conferences c on c.id = p.conference_id
            left join tracks t on t.id = p.track_id
            where p.id = %s
            """,
            (paper_id,),
        )
        authors = self.queries.list_rows(
            "select author_order, display_name, affiliation from paper_authors where paper_id = %s order by author_order asc",
            (paper_id,),
        )
        artifacts = self.queries.list_rows(
            "select artifact_kind, label, resolution_reason, resolved_url, download_status, local_path from artifacts where paper_id = %s order by created_at asc",
            (paper_id,),
        )
        parse = self.queries.get_row(
            "select * from paper_parses where paper_id = %s order by created_at desc limit 1",
            (paper_id,),
        )
        chunks = self.queries.list_rows(
            "select chunk_index, token_count, left(content, 1200) as content from paper_chunks where paper_id = %s order by chunk_index asc limit 12",
            (paper_id,),
        )
        summary = self.queries.get_row(
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
        return self.queries.list_rows(
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
            left join lateral (
                select * from paper_summaries ps where ps.paper_id = p.id order by ps.created_at desc limit 1
            ) ls on true
            left join lateral (
                select * from paper_parses pp where pp.paper_id = p.id order by pp.created_at desc limit 1
            ) lp on true
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
            label: self.queries.get_row(query, ())["count"]
            for label, query in counts.items()
        }
        result["summary_providers"] = self.queries.list_rows(
            "select provider, count(*) as count from paper_summaries group by provider order by count desc"
        )
        result["artifact_kinds"] = self.queries.list_rows(
            "select artifact_kind, count(*) as count from artifacts group by artifact_kind order by count desc"
        )
        result["failed_job_types"] = self.queries.list_rows(
            "select job_type, count(*) as count from jobs where status = 'failed' group by job_type order by count desc"
        )
        result["fallback_reasons"] = self.queries.list_rows(
            """
            select coalesce(a.resolution_reason, 'no_reason_recorded') as reason, count(*) as count
            from artifacts a
            where a.artifact_kind = 'fallback_to_arxiv'
            group by coalesce(a.resolution_reason, 'no_reason_recorded')
            order by count desc
            """
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
        return self.queries.list_rows(
            f"select id, job_type, status, priority, attempt_count, max_attempts, last_error, updated_at from jobs {where_sql} order by updated_at desc limit %s",
            tuple([*params, limit]),
        )

    def list_conferences(self) -> list[dict[str, Any]]:
        return self.queries.list_rows(
            "select * from conferences order by year desc, name asc"
        )

    def list_tracks(self) -> list[dict[str, Any]]:
        return self.queries.list_rows(
            """
            select t.*, c.slug as conference_slug
            from tracks t join conferences c on c.id = t.conference_id
            order by c.year desc, t.name asc
            """
        )

    def list_api_papers(self, *, limit: int) -> list[dict[str, Any]]:
        return self.queries.list_rows(
            """
            select p.*, c.slug as conference_slug, t.slug as track_slug
            from papers p
            join conferences c on c.id = p.conference_id
            left join tracks t on t.id = p.track_id
            order by p.created_at desc
            limit %s
            """,
            (limit,),
        )

    def get_api_paper(self, *, paper_id: str) -> dict[str, Any]:
        paper = self.queries.get_row("select * from papers where id = %s", (paper_id,))
        authors = self.queries.list_rows(
            "select author_order, display_name, affiliation, orcid from paper_authors where paper_id = %s order by author_order asc",
            (paper_id,),
        )
        parses = self.queries.list_rows(
            "select id, parser_version, parse_status, page_count, created_at from paper_parses where paper_id = %s order by created_at desc",
            (paper_id,),
        )
        summaries = self.queries.list_rows(
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
        rows = self.queries.list_rows(
            """
            select content
            from paper_chunks
            where paper_id = %s
            order by ts_rank_cd(to_tsvector('english', coalesce(content, '')), plainto_tsquery('english', %s)) desc,
                     chunk_index asc
            limit %s
            """,
            (paper_id, question, limit),
        )
        summary = self.queries.get_row(
            "select * from paper_summaries where paper_id = %s order by created_at desc limit 1",
            (paper_id,),
        )
        return [row["content"] for row in rows], summary

    def get_library_question_context(
        self, *, question: str, limit: int
    ) -> tuple[list[str], dict[str, Any] | None, list[dict[str, Any]]]:
        rows = self.queries.list_rows(
            """
            select p.id as paper_id, p.canonical_title, pc.content
            from paper_chunks pc
            join papers p on p.id = pc.paper_id
            order by ts_rank_cd(to_tsvector('english', coalesce(pc.content, '')), plainto_tsquery('english', %s)) desc,
                     pc.created_at asc
            limit %s
            """,
            (question, limit),
        )
        context_chunks = [
            f"[{row['canonical_title']}] {row['content']}" for row in rows
        ]
        summary = None
        if rows:
            summary = self.queries.get_row(
                "select * from paper_summaries where paper_id = %s order by created_at desc limit 1",
                (rows[0]["paper_id"],),
            )
        return context_chunks, summary, rows


def build_paper_order_sql(sort: str, order: str) -> str:
    direction = "desc" if order.lower() == "desc" else "asc"
    if sort == "title":
        return f"p.canonical_title {direction}, p.updated_at desc"
    if sort == "year":
        return f"p.year {direction}, p.updated_at desc"
    if sort == "updated":
        return f"p.updated_at {direction}"
    return "(ls.id is not null) desc, p.updated_at desc"
