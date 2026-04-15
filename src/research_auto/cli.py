from __future__ import annotations

import argparse
import json

import uvicorn

from research_auto.api import create_app
from research_auto.config import get_settings
from research_auto.db import Database
from research_auto.jobs import JobWorker
from research_auto.llm import (
    PROMPT_VERSION,
    build_provider,
    fallback_answer_from_summary,
)


ICSE_2026_TRACK_URL = "https://conf.researchr.org/track/icse-2026/icse-2026-research-track?#event-overview"
ICSE_2026_HOME_URL = "https://conf.researchr.org/home/icse-2026"


def bootstrap_db() -> None:
    settings = get_settings()
    Database(settings.database_url).bootstrap()
    print("database bootstrapped")


def seed_icse() -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    conference = db.upsert_conference(
        slug="icse-2026",
        name="ICSE 2026",
        year=2026,
        homepage_url=ICSE_2026_HOME_URL,
        source_system="researchr",
    )
    track = db.upsert_track(
        conference_id=conference["id"],
        slug="research-track",
        name="Research Track",
        track_url=ICSE_2026_TRACK_URL,
    )
    db.enqueue_job(
        job_type="crawl_track",
        payload={
            "conference_id": conference["id"],
            "track_id": track["id"],
            "track_url": track["track_url"],
            "year": conference["year"],
            "paper_type": "research",
        },
        dedupe_key=f"crawl_track:{track['id']}",
        priority=10,
    )
    print(f"seeded {conference['slug']} / {track['slug']}")


def enqueue_resolve(limit: int | None) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    query = """
        select p.id, p.detail_url
        from papers p
        where p.best_pdf_url is null
          and not exists (select 1 from paper_parses pp where pp.paper_id = p.id)
          and not exists (select 1 from paper_summaries ps where ps.paper_id = p.id)
          and not exists (
              select 1 from jobs j
              where j.dedupe_key = 'resolve_paper_artifacts:' || p.id::text
                and j.status in ('pending', 'running')
          )
        order by p.canonical_title asc
    """
    params: tuple[object, ...] = ()
    if limit is not None:
        query += " limit %s"
        params = (limit,)
    rows = db.list_rows(query, params)
    inserted = 0
    for row in rows:
        did_insert = db.enqueue_job(
            job_type="resolve_paper_artifacts",
            payload={"paper_id": row["id"], "detail_url": row["detail_url"]},
            dedupe_key=f"resolve_paper_artifacts:{row['id']}",
            priority=20,
        )
        if did_insert:
            inserted += 1
    print(f"enqueued {inserted} resolve jobs")


def repair_resolution_status() -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    before = db.get_row(
        """
        select count(*) as count
        from papers
        where resolution_status = 'resolved' and best_pdf_url is null
        """,
        (),
    )["count"]
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update papers
                set resolution_status = 'unresolved'
                where resolution_status = 'resolved' and best_pdf_url is null
                """
            )
        conn.commit()
    after = db.get_row(
        """
        select count(*) as count
        from papers
        where resolution_status = 'resolved' and best_pdf_url is null
        """,
        (),
    )["count"]
    print(f"repaired {before - after} papers")


def enqueue_parse(limit: int | None) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    query = """
        select id, paper_id, local_path
        from artifacts
        where download_status = 'downloaded'
          and local_path is not null
          and (mime_type = 'application/pdf' or lower(local_path) like '%%.pdf')
        order by downloaded_at asc nulls last
    """
    params: tuple[object, ...] = ()
    if limit is not None:
        query += " limit %s"
        params = (limit,)
    rows = db.list_rows(query, params)
    for row in rows:
        db.enqueue_job(
            job_type="parse_artifact",
            payload={
                "paper_id": row["paper_id"],
                "artifact_id": row["id"],
                "local_path": row["local_path"],
            },
            dedupe_key=f"parse_artifact:{row['id']}",
            priority=40,
        )
    print(f"enqueued {len(rows)} parse jobs")


def enqueue_summarize(limit: int | None) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    query = "select id, paper_id from paper_parses order by created_at asc"
    params: tuple[object, ...] = ()
    if limit is not None:
        query += " limit %s"
        params = (limit,)
    rows = db.list_rows(query, params)
    for row in rows:
        db.enqueue_job(
            job_type="summarize_paper",
            payload={"paper_id": row["paper_id"], "paper_parse_id": row["id"]},
            dedupe_key=f"summarize_paper:{row['id']}:{settings.llm_provider}:{settings.llm_model}:{PROMPT_VERSION}",
            priority=50,
        )
    print(f"enqueued {len(rows)} summarize jobs")


def enqueue_resummarize_fallbacks(limit: int | None) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    query = """
        select distinct on (paper_parse_id) paper_parse_id as id, paper_id
        from paper_summaries
        where provider like '%%fallback'
        order by paper_parse_id, created_at desc
    """
    params: tuple[object, ...] = ()
    if limit is not None:
        query += " limit %s"
        params = (limit,)
    rows = db.list_rows(query, params)
    for row in rows:
        db.enqueue_job(
            job_type="summarize_paper",
            payload={"paper_id": row["paper_id"], "paper_parse_id": row["id"]},
            dedupe_key=f"resummarize_paper:{row['id']}:{settings.llm_provider}:{settings.llm_model}:{PROMPT_VERSION}",
            priority=45,
            max_attempts=10,
        )
    print(f"enqueued {len(rows)} fallback re-summarize jobs")


def search_papers_cli(query: str, limit: int) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    like_q = f"%{query}%"
    rows = db.list_rows(
        """
        select distinct on (p.id)
            p.id,
            p.canonical_title,
            s.summary_short,
            s.summary_short_zh,
            s.research_question_zh,
            p.best_pdf_url,
            ts_rank_cd(
                setweight(to_tsvector('english', coalesce(p.canonical_title, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(p.abstract, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(s.summary_short, '')), 'B') ||
                setweight(to_tsvector('simple', coalesce(s.summary_short_zh, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(pp.full_text, '')), 'C'),
                plainto_tsquery('english', %s)
            ) as rank
        from papers p
        left join paper_summaries s on s.paper_id = p.id
        left join paper_parses pp on pp.paper_id = p.id
        where (
            setweight(to_tsvector('english', coalesce(p.canonical_title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(p.abstract, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(s.summary_short, '')), 'B') ||
            setweight(to_tsvector('simple', coalesce(s.summary_short_zh, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(pp.full_text, '')), 'C')
        ) @@ plainto_tsquery('english', %s)
        or p.canonical_title ilike %s
        or coalesce(p.abstract, '') ilike %s
        or coalesce(s.summary_short, '') ilike %s
        or coalesce(s.summary_short_zh, '') ilike %s
        order by p.id, rank desc nulls last, s.created_at desc nulls last
        limit %s
        """,
        (query, query, like_q, like_q, like_q, like_q, limit),
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))


def show_paper_cli(paper_id: str) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    paper = db.get_row("select * from papers where id = %s", (paper_id,))
    authors = db.list_rows(
        "select author_order, display_name from paper_authors where paper_id = %s order by author_order asc",
        (paper_id,),
    )
    summary = db.get_row(
        """
        select *
        from paper_summaries
        where paper_id = %s
        order by created_at desc
        limit 1
        """,
        (paper_id,),
    )
    payload = {"paper": paper, "authors": authors, "summary": summary}
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def ask_paper_cli(paper_id: str, question: str, limit: int) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    provider = build_provider(settings)
    rows = db.list_rows(
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
    context_chunks = [row["content"] for row in rows]
    try:
        answer = provider.answer_question(
            question=question,
            paper_context="\n\n---\n\n".join(context_chunks),
            chunk_quotes=context_chunks,
        )
    except Exception:
        summary = db.get_row(
            "select * from paper_summaries where paper_id = %s order by created_at desc limit 1",
            (paper_id,),
        )
        answer = fallback_answer_from_summary(
            question=question, summary_row=summary, chunk_quotes=context_chunks
        )
    print(
        json.dumps(
            {
                "answer": answer.answer,
                "answer_zh": answer.answer_zh,
                "evidence_quotes": answer.evidence_quotes,
                "confidence": answer.confidence,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def ask_library_cli(question: str, limit: int) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    provider = build_provider(settings)
    rows = db.list_rows(
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
    context_chunks = [f"[{row['canonical_title']}] {row['content']}" for row in rows]
    try:
        answer = provider.answer_question(
            question=question,
            paper_context="\n\n---\n\n".join(context_chunks),
            chunk_quotes=context_chunks,
        )
    except Exception:
        summary = None
        if rows:
            summary = db.get_row(
                "select * from paper_summaries where paper_id = %s order by created_at desc limit 1",
                (rows[0]["paper_id"],),
            )
        answer = fallback_answer_from_summary(
            question=question, summary_row=summary, chunk_quotes=context_chunks
        )
    print(
        json.dumps(
            {
                "answer": answer.answer,
                "answer_zh": answer.answer_zh,
                "evidence_quotes": answer.evidence_quotes,
                "confidence": answer.confidence,
                "papers": dedupe_papers_cli(rows),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def run_worker(once: bool, queue: str | None) -> None:
    settings = get_settings()
    worker = JobWorker(Database(settings.database_url), settings, queue_name=queue)
    if once:
        processed = worker.run_once()
        print("processed one job" if processed else "no jobs available")
        return
    worker.run_forever()


def drain_worker(queue: str | None) -> None:
    settings = get_settings()
    worker = JobWorker(Database(settings.database_url), settings, queue_name=queue)
    processed = worker.drain()
    print(f"processed {processed} jobs")


def run_api(host: str, port: int) -> None:
    uvicorn.run(create_app(), host=host, port=port)


def dedupe_papers_cli(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for row in rows:
        paper_id = str(row["paper_id"])
        if paper_id in seen:
            continue
        seen.add(paper_id)
        result.append(
            {"paper_id": row["paper_id"], "canonical_title": row["canonical_title"]}
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-auto")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("bootstrap-db")
    subparsers.add_parser("seed-icse")
    drain_parser = subparsers.add_parser("drain")
    drain_parser.add_argument("--queue")
    subparsers.add_parser("repair-resolution-status")

    resolve_parser = subparsers.add_parser("enqueue-resolve")
    resolve_parser.add_argument("--limit", type=int)

    parse_parser = subparsers.add_parser("enqueue-parse")
    parse_parser.add_argument("--limit", type=int)

    summarize_parser = subparsers.add_parser("enqueue-summarize")
    summarize_parser.add_argument("--limit", type=int)

    resummarize_parser = subparsers.add_parser("enqueue-resummarize-fallbacks")
    resummarize_parser.add_argument("--limit", type=int)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)

    show_parser = subparsers.add_parser("show-paper")
    show_parser.add_argument("paper_id")

    ask_paper_parser = subparsers.add_parser("ask-paper")
    ask_paper_parser.add_argument("paper_id")
    ask_paper_parser.add_argument("question")
    ask_paper_parser.add_argument("--limit", type=int, default=8)

    ask_library_parser = subparsers.add_parser("ask-library")
    ask_library_parser.add_argument("question")
    ask_library_parser.add_argument("--limit", type=int, default=8)

    worker_parser = subparsers.add_parser("worker")
    worker_parser.add_argument("--once", action="store_true")
    worker_parser.add_argument("--queue")

    api_parser = subparsers.add_parser("api")
    api_parser.add_argument("--host", default="127.0.0.1")
    api_parser.add_argument("--port", type=int, default=8000)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "bootstrap-db":
        bootstrap_db()
    elif args.command == "seed-icse":
        seed_icse()
    elif args.command == "drain":
        drain_worker(args.queue)
    elif args.command == "repair-resolution-status":
        repair_resolution_status()
    elif args.command == "enqueue-resolve":
        enqueue_resolve(args.limit)
    elif args.command == "enqueue-parse":
        enqueue_parse(args.limit)
    elif args.command == "enqueue-summarize":
        enqueue_summarize(args.limit)
    elif args.command == "enqueue-resummarize-fallbacks":
        enqueue_resummarize_fallbacks(args.limit)
    elif args.command == "search":
        search_papers_cli(args.query, args.limit)
    elif args.command == "show-paper":
        show_paper_cli(args.paper_id)
    elif args.command == "ask-paper":
        ask_paper_cli(args.paper_id, args.question, args.limit)
    elif args.command == "ask-library":
        ask_library_cli(args.question, args.limit)
    elif args.command == "worker":
        run_worker(args.once, args.queue)
    elif args.command == "api":
        run_api(args.host, args.port)
