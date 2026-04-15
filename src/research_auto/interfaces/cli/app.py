from __future__ import annotations

import argparse
import json

import uvicorn

from research_auto.infrastructure.postgres.database import Database
from research_auto.infrastructure.postgres.repositories import (
    PostgresCatalogRepository,
    PostgresJobRepository,
    PostgresReadRepository,
)
from research_auto.application.query_services import (
    QuestionAnswerService,
    ReadQueryService,
)
from research_auto.interfaces.api.app import create_app
from research_auto.config import get_settings
from research_auto.application.queue_policies import get_queue_policy
from research_auto.interfaces.worker.runner import JobWorker
from research_auto.application.llm import PROMPT_VERSION
from research_auto.infrastructure.llm.provider import build_provider


ICSE_2026_TRACK_URL = "https://conf.researchr.org/track/icse-2026/icse-2026-research-track?#event-overview"
ICSE_2026_HOME_URL = "https://conf.researchr.org/home/icse-2026"


def bootstrap_db() -> None:
    settings = get_settings()
    Database(settings.database_url).bootstrap()
    print("database bootstrapped")


def seed_icse() -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    catalog = PostgresCatalogRepository(db)
    jobs = PostgresJobRepository(db)
    conference = catalog.upsert_conference(
        slug="icse-2026",
        name="ICSE 2026",
        year=2026,
        homepage_url=ICSE_2026_HOME_URL,
        source_system="researchr",
    )
    track = catalog.upsert_track(
        conference_id=conference["id"],
        slug="research-track",
        name="Research Track",
        track_url=ICSE_2026_TRACK_URL,
    )
    jobs.enqueue_job(
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
    queries = PostgresJobRepository(db)
    jobs = queries
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
    rows = queries.fetch_all(query, params)
    inserted = 0
    for row in rows:
        did_insert = jobs.enqueue_job(
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
    queries = PostgresJobRepository(db)
    before = queries.fetch_one(
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
    after = queries.fetch_one(
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
    queries = PostgresJobRepository(db)
    jobs = queries
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
    rows = queries.fetch_all(query, params)
    for row in rows:
        jobs.enqueue_job(
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
    queries = PostgresJobRepository(db)
    jobs = queries
    query = "select id, paper_id from paper_parses order by created_at asc"
    params: tuple[object, ...] = ()
    if limit is not None:
        query += " limit %s"
        params = (limit,)
    rows = queries.fetch_all(query, params)
    for row in rows:
        jobs.enqueue_job(
            job_type="summarize_paper",
            payload={"paper_id": row["paper_id"], "paper_parse_id": row["id"]},
            dedupe_key=f"summarize_paper:{row['id']}:{settings.llm_provider}:{settings.llm_model}:{PROMPT_VERSION}",
            priority=50,
        )
    print(f"enqueued {len(rows)} summarize jobs")


def enqueue_resummarize_fallbacks(limit: int | None) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    queries = PostgresJobRepository(db)
    jobs = queries
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
    rows = queries.fetch_all(query, params)
    for row in rows:
        jobs.enqueue_job(
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
    rows = ReadQueryService(PostgresReadRepository(db)).search_papers(query, limit)
    print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))


def show_paper_cli(paper_id: str) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    payload = ReadQueryService(PostgresReadRepository(db)).get_paper_detail(paper_id)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def ask_paper_cli(paper_id: str, question: str, limit: int) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    service = QuestionAnswerService(
        PostgresReadRepository(db), build_provider(settings)
    )
    print(
        json.dumps(
            service.ask_paper(paper_id=paper_id, question=question, limit=limit),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def ask_library_cli(question: str, limit: int) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    service = QuestionAnswerService(
        PostgresReadRepository(db), build_provider(settings)
    )
    print(
        json.dumps(
            service.ask_library(question=question, limit=limit),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def run_worker(once: bool, queue: str | None) -> None:
    settings = get_settings()
    if queue is not None:
        get_queue_policy(queue)
    worker = JobWorker(Database(settings.database_url), settings, queue_name=queue)
    if once:
        processed = worker.run_once()
        print("processed one job" if processed else "no jobs available")
        return
    worker.run_forever()


def drain_worker(queue: str | None) -> None:
    settings = get_settings()
    if queue is not None:
        get_queue_policy(queue)
    worker = JobWorker(Database(settings.database_url), settings, queue_name=queue)
    processed = worker.drain()
    print(f"processed {processed} jobs")


def run_api(host: str, port: int) -> None:
    uvicorn.run(create_app(), host=host, port=port)


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
