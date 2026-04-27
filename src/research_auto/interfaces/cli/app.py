from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv
import uvicorn

from research_auto.infrastructure.postgres.database import Database
from research_auto.infrastructure.postgres.repositories import PostgresReadRepository
from research_auto.application.admin_actions import (
    bootstrap_db as bootstrap_db_action,
    drain_worker as drain_worker_action,
    enqueue_parse as enqueue_parse_action,
    enqueue_resolve as enqueue_resolve_action,
    enqueue_resummarize_fallbacks as enqueue_resummarize_fallbacks_action,
    enqueue_summarize as enqueue_summarize_action,
    migrate_db as migrate_db_action,
    repair_resolution_status as repair_resolution_status_action,
    repair_running_jobs as repair_running_jobs_action,
    seed_icse as seed_icse_action,
)
from research_auto.application.query_services import (
    QuestionAnswerService,
    ReadQueryService,
)
from research_auto.application.queue_policies import get_queue_policy
from research_auto.interfaces.api.app import create_app
from research_auto.config import get_settings
from research_auto.interfaces.worker.runner import JobWorker
from research_auto.infrastructure.llm.provider import build_provider


def bootstrap_db() -> None:
    settings = get_settings()
    bootstrap_db_action(settings)
    print("database migrated")


def migrate_db() -> None:
    settings = get_settings()
    applied = migrate_db_action(settings)
    if applied:
        print(f"applied {applied} migrations")
    else:
        print("database already up to date")


def seed_icse() -> None:
    settings = get_settings()
    result = seed_icse_action(settings)
    print(f"seeded {result['conference_slug']} / {result['track_slug']}")


def enqueue_resolve(limit: int | None) -> None:
    settings = get_settings()
    inserted = enqueue_resolve_action(settings, limit)
    print(f"enqueued {inserted} resolve jobs")


def repair_resolution_status() -> None:
    settings = get_settings()
    repaired = repair_resolution_status_action(settings)
    print(f"repaired {repaired} papers")


def repair_running_jobs(older_than_seconds: int) -> None:
    settings = get_settings()
    repaired = repair_running_jobs_action(settings, older_than_seconds)
    print(f"repaired {repaired} running jobs older than {older_than_seconds} seconds")


def enqueue_parse(limit: int | None) -> None:
    settings = get_settings()
    count = enqueue_parse_action(settings, limit)
    print(f"enqueued {count} parse jobs")


def enqueue_summarize(limit: int | None) -> None:
    settings = get_settings()
    count = enqueue_summarize_action(settings, limit)
    print(f"enqueued {count} summarize jobs")


def enqueue_resummarize_fallbacks(limit: int | None) -> None:
    settings = get_settings()
    count = enqueue_resummarize_fallbacks_action(settings, limit)
    print(f"enqueued {count} fallback re-summarize jobs")


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
    processed = drain_worker_action(settings, queue)
    print(f"processed {processed} jobs")


def run_api(host: str, port: int) -> None:
    uvicorn.run(create_app(), host=host, port=port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-auto")
    subparsers = parser.add_subparsers(dest="group", required=True)

    setup_parser = subparsers.add_parser("setup")
    setup_subparsers = setup_parser.add_subparsers(dest="command", required=True)
    setup_subparsers.add_parser("bootstrap-db")
    setup_subparsers.add_parser("migrate")
    setup_subparsers.add_parser("seed-icse")

    pipeline_parser = subparsers.add_parser("pipeline")
    pipeline_subparsers = pipeline_parser.add_subparsers(dest="command", required=True)

    resolve_parser = pipeline_subparsers.add_parser("resolve")
    resolve_parser.add_argument("--limit", type=int)

    parse_parser = pipeline_subparsers.add_parser("parse")
    parse_parser.add_argument("--limit", type=int)

    summarize_parser = pipeline_subparsers.add_parser("summarize")
    summarize_parser.add_argument("--limit", type=int)

    resummarize_parser = pipeline_subparsers.add_parser("resummarize-fallbacks")
    resummarize_parser.add_argument("--limit", type=int)

    pipeline_subparsers.add_parser("repair-resolution-status")

    repair_running_parser = pipeline_subparsers.add_parser("repair-running-jobs")
    repair_running_parser.add_argument("--older-than-seconds", type=int, default=900)

    drain_parser = pipeline_subparsers.add_parser("drain")
    drain_parser.add_argument("--queue")

    inspect_parser = subparsers.add_parser("inspect")
    inspect_subparsers = inspect_parser.add_subparsers(dest="command", required=True)

    search_parser = inspect_subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)

    inspect_subparsers.add_parser("paper").add_argument("paper_id")

    ask_parser = inspect_subparsers.add_parser("ask")
    ask_subparsers = ask_parser.add_subparsers(dest="target", required=True)

    ask_paper_parser = ask_subparsers.add_parser("paper")
    ask_paper_parser.add_argument("paper_id")
    ask_paper_parser.add_argument("question")
    ask_paper_parser.add_argument("--limit", type=int, default=8)

    ask_library_parser = ask_subparsers.add_parser("library")
    ask_library_parser.add_argument("question")
    ask_library_parser.add_argument("--limit", type=int, default=8)

    serve_parser = subparsers.add_parser("serve")
    serve_subparsers = serve_parser.add_subparsers(dest="command", required=True)

    worker_parser = serve_subparsers.add_parser("worker")
    worker_parser.add_argument("--once", action="store_true")
    worker_parser.add_argument("--queue")

    api_parser = serve_subparsers.add_parser("api")
    api_parser.add_argument("--host", default="127.0.0.1")
    api_parser.add_argument("--port", type=int, default=8000)

    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    match args.group:
        case "setup":
            if args.command == "bootstrap-db":
                bootstrap_db()
            elif args.command == "migrate":
                migrate_db()
            elif args.command == "seed-icse":
                seed_icse()
        case "pipeline":
            if args.command == "resolve":
                enqueue_resolve(args.limit)
            elif args.command == "parse":
                enqueue_parse(args.limit)
            elif args.command == "summarize":
                enqueue_summarize(args.limit)
            elif args.command == "resummarize-fallbacks":
                enqueue_resummarize_fallbacks(args.limit)
            elif args.command == "repair-resolution-status":
                repair_resolution_status()
            elif args.command == "repair-running-jobs":
                repair_running_jobs(args.older_than_seconds)
            elif args.command == "drain":
                drain_worker(args.queue)
        case "inspect":
            if args.command == "search":
                search_papers_cli(args.query, args.limit)
            elif args.command == "paper":
                show_paper_cli(args.paper_id)
            elif args.command == "ask":
                if args.target == "paper":
                    ask_paper_cli(args.paper_id, args.question, args.limit)
                elif args.target == "library":
                    ask_library_cli(args.question, args.limit)
        case "serve":
            if args.command == "worker":
                run_worker(args.once, args.queue)
            elif args.command == "api":
                run_api(args.host, args.port)
