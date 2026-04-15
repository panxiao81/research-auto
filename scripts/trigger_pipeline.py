from research_auto.cli import (
    bootstrap_db,
    drain_worker,
    enqueue_parse,
    enqueue_resolve,
    enqueue_summarize,
    run_worker,
    seed_icse,
)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Trigger the research-auto pipeline from CLI")
    parser.add_argument("--bootstrap-db", action="store_true", help="Create database schema")
    parser.add_argument("--seed-icse", action="store_true", help="Seed ICSE 2026 Research Track crawl job")
    parser.add_argument("--enqueue-resolve", action="store_true", help="Enqueue artifact resolution jobs")
    parser.add_argument("--resolve-limit", type=int, help="Limit papers to resolve")
    parser.add_argument("--enqueue-parse", action="store_true", help="Enqueue PDF parse jobs")
    parser.add_argument("--parse-limit", type=int, help="Limit artifacts to parse")
    parser.add_argument("--enqueue-summarize", action="store_true", help="Enqueue summary jobs")
    parser.add_argument("--summarize-limit", type=int, help="Limit parses to summarize")
    parser.add_argument("--worker-once", action="store_true", help="Process a single queued job")
    parser.add_argument("--drain", action="store_true", help="Process jobs until queue is empty")
    args = parser.parse_args()

    if args.bootstrap_db:
        bootstrap_db()
    if args.seed_icse:
        seed_icse()
    if args.enqueue_resolve:
        enqueue_resolve(args.resolve_limit)
    if args.enqueue_parse:
        enqueue_parse(args.parse_limit)
    if args.enqueue_summarize:
        enqueue_summarize(args.summarize_limit)
    if args.worker_once:
        run_worker(True)
    if args.drain:
        drain_worker()

    if not any((args.bootstrap_db, args.seed_icse, args.enqueue_resolve, args.enqueue_parse, args.enqueue_summarize, args.worker_once, args.drain)):
        parser.print_help()


if __name__ == "__main__":
    main()
