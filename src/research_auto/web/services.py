from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from research_auto.db import Database


@dataclass(slots=True)
class Page:
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        return max(1, ceil(self.total / self.page_size)) if self.page_size else 1


def list_papers_for_ui(
    db: Database,
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
            select pp.*
            from paper_parses pp
            where pp.paper_id = p.id
            order by pp.created_at desc
            limit 1
        ) lp on true
        left join lateral (
            select ps.*
            from paper_summaries ps
            where ps.paper_id = p.id
            order by ps.created_at desc
            limit 1
        ) ls on true
        {where_sql}
    """

    total = db.get_row(f"select count(*) as count {base_from}", tuple(params))["count"]
    rows = db.list_rows(
        f"""
        select
            p.id,
            p.canonical_title,
            p.year,
            p.session_name,
            p.best_pdf_url,
            p.resolution_status,
            p.updated_at,
            c.slug as conference_slug,
            t.name as track_name,
            lp.id as latest_parse_id,
            lp.page_count,
            ls.id as latest_summary_id,
            ls.provider,
            ls.model_name,
            ls.prompt_version,
            ls.summary_short,
            ls.summary_short_zh,
            ls.research_question_zh,
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


def build_paper_order_sql(sort: str, order: str) -> str:
    direction = "desc" if order.lower() == "desc" else "asc"
    if sort == "title":
        return f"p.canonical_title {direction}, p.updated_at desc"
    if sort == "year":
        return f"p.year {direction}, p.updated_at desc"
    if sort == "updated":
        return f"p.updated_at {direction}"
    return "(ls.id is not null) desc, p.updated_at desc"


def get_paper_detail_for_ui(db: Database, paper_id: str) -> dict[str, Any]:
    paper = db.get_row(
        """
        select p.*, c.name as conference_name, c.slug as conference_slug, t.name as track_name
        from papers p
        join conferences c on c.id = p.conference_id
        left join tracks t on t.id = p.track_id
        where p.id = %s
        """,
        (paper_id,),
    )
    authors = db.list_rows(
        "select author_order, display_name, affiliation from paper_authors where paper_id = %s order by author_order asc",
        (paper_id,),
    )
    artifacts = db.list_rows(
        "select artifact_kind, label, resolved_url, download_status, local_path from artifacts where paper_id = %s order by created_at asc",
        (paper_id,),
    )
    parse = db.get_row(
        "select * from paper_parses where paper_id = %s order by created_at desc limit 1",
        (paper_id,),
    )
    chunks = db.list_rows(
        "select chunk_index, token_count, left(content, 1200) as content from paper_chunks where paper_id = %s order by chunk_index asc limit 12",
        (paper_id,),
    )
    summary = db.get_row(
        "select * from paper_summaries where paper_id = %s order by created_at desc limit 1",
        (paper_id,),
    )
    bibtex = build_bibtex_for_ui(paper=paper, authors=authors)
    return {
        "paper": paper,
        "authors": authors,
        "artifacts": artifacts,
        "parse": parse,
        "chunks": chunks,
        "summary": summary,
        "bibtex": bibtex,
    }


def build_bibtex_for_ui(*, paper: dict[str, Any], authors: list[dict[str, Any]]) -> str:
    cite_key = build_bibtex_key(paper=paper, authors=authors)
    fields: list[tuple[str, str]] = []

    author_names = [
        str(author.get("display_name") or "").strip()
        for author in authors
        if str(author.get("display_name") or "").strip()
    ]
    if author_names:
        fields.append(("author", " and ".join(author_names)))

    fields.append(("title", str(paper.get("canonical_title") or "")))
    fields.append(("booktitle", str(paper.get("conference_name") or "")))
    fields.append(("year", str(paper.get("year") or "")))

    doi = str(paper.get("doi") or "").strip()
    if doi:
        fields.append(("doi", doi))

    url = str(
        paper.get("best_landing_url")
        or paper.get("best_pdf_url")
        or paper.get("detail_url")
        or ""
    ).strip()
    if url:
        fields.append(("url", url))

    rendered_fields = []
    for key, value in fields:
        escaped = value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        rendered_fields.append(f"  {key} = {{{escaped}}}")
    return "@inproceedings{" + cite_key + ",\n" + ",\n".join(rendered_fields) + "\n}"


def build_bibtex_key(*, paper: dict[str, Any], authors: list[dict[str, Any]]) -> str:
    first_author = "paper"
    if authors:
        first_name = str(authors[0].get("display_name") or "").strip().split()
        if first_name:
            first_author = sanitize_bibtex_token(first_name[-1])

    year = str(paper.get("year") or "")
    title_words = [
        sanitize_bibtex_token(part)
        for part in str(paper.get("canonical_title") or "").split()
    ]
    title_token = next((word for word in title_words if word), "paper")
    return f"{first_author}{year}{title_token}"


def sanitize_bibtex_token(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isalnum())
    return cleaned[:32].lower()


def search_papers_for_ui(db: Database, q: str, limit: int) -> list[dict[str, Any]]:
    like_q = f"%{q}%"
    return db.list_rows(
        """
        select distinct on (p.id)
            p.id,
            p.canonical_title,
            p.best_pdf_url,
            p.resolution_status,
            ls.summary_short,
            ls.summary_short_zh,
            ls.research_question,
            ls.research_question_zh,
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


def get_ui_stats(db: Database) -> dict[str, Any]:
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
    result = {label: db.get_row(query, ())["count"] for label, query in counts.items()}
    result["summary_providers"] = db.list_rows(
        "select provider, count(*) as count from paper_summaries group by provider order by count desc"
    )
    result["artifact_kinds"] = db.list_rows(
        "select artifact_kind, count(*) as count from artifacts group by artifact_kind order by count desc"
    )
    result["failed_job_types"] = db.list_rows(
        "select job_type, count(*) as count from jobs where status = 'failed' group by job_type order by count desc"
    )
    result["fallback_reasons"] = db.list_rows(
        """
        select coalesce(a.resolution_reason, 'no_reason_recorded') as reason, count(*) as count
        from artifacts a
        where a.artifact_kind = 'fallback_to_arxiv'
        group by coalesce(a.resolution_reason, 'no_reason_recorded')
        order by count desc
        """
    )
    return result


def list_jobs_for_ui(
    db: Database, *, status: str | None, job_type: str | None, limit: int
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
    return db.list_rows(
        f"select id, job_type, status, priority, attempt_count, max_attempts, last_error, updated_at from jobs {where_sql} order by updated_at desc limit %s",
        tuple([*params, limit]),
    )
