from __future__ import annotations

from research_auto.application.llm import PROMPT_VERSION
from research_auto.application.queue_policies import get_queue_policy
from research_auto.config import Settings
from research_auto.infrastructure.postgres.database import Database
from research_auto.infrastructure.postgres.repositories import (
    PostgresCatalogRepository,
    PostgresJobRepository,
)
from research_auto.interfaces.worker.runner import JobWorker


ICSE_2026_TRACK_URL = "https://conf.researchr.org/track/icse-2026/icse-2026-research-track?#event-overview"
ICSE_2026_HOME_URL = "https://conf.researchr.org/home/icse-2026"


def bootstrap_db(settings: Settings) -> None:
    Database(settings.database_url).bootstrap()


def seed_icse(settings: Settings) -> dict[str, str]:
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
    return {"conference_slug": conference["slug"], "track_slug": track["slug"]}


def enqueue_resolve(settings: Settings, limit: int | None) -> int:
    db = Database(settings.database_url)
    jobs = PostgresJobRepository(db)
    rows = jobs.list_papers_needing_resolution(limit=limit)
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
    return inserted


def repair_resolution_status(settings: Settings) -> int:
    db = Database(settings.database_url)
    return PostgresJobRepository(db).repair_resolved_without_pdf()


def enqueue_parse(settings: Settings, limit: int | None) -> int:
    db = Database(settings.database_url)
    jobs = PostgresJobRepository(db)
    rows = jobs.list_downloaded_pdf_artifacts(limit=limit)
    for row in rows:
        jobs.enqueue_job(
            job_type="parse_artifact",
            payload={
                "paper_id": row["paper_id"],
                "artifact_id": row["id"],
                "storage_uri": row["storage_uri"],
                "checksum_sha256": row["checksum_sha256"],
            },
            dedupe_key=f"parse_artifact:{row['id']}:{row['checksum_sha256']}",
            priority=40,
        )
    return len(rows)


def enqueue_summarize(settings: Settings, limit: int | None) -> int:
    db = Database(settings.database_url)
    jobs = PostgresJobRepository(db)
    rows = jobs.list_paper_parses(limit=limit)
    for row in rows:
        jobs.enqueue_job(
            job_type="summarize_paper",
            payload={"paper_id": row["paper_id"], "paper_parse_id": row["id"]},
            dedupe_key=f"summarize_paper:{row['id']}:{settings.llm_provider}:{settings.llm_model}:{PROMPT_VERSION}",
            priority=50,
        )
    return len(rows)


def enqueue_resummarize_fallbacks(settings: Settings, limit: int | None) -> int:
    db = Database(settings.database_url)
    jobs = PostgresJobRepository(db)
    rows = jobs.list_fallback_summaries(limit=limit)
    for row in rows:
        jobs.enqueue_job(
            job_type="summarize_paper",
            payload={"paper_id": row["paper_id"], "paper_parse_id": row["id"]},
            dedupe_key=f"resummarize_paper:{row['id']}:{settings.llm_provider}:{settings.llm_model}:{PROMPT_VERSION}",
            priority=45,
            max_attempts=10,
        )
    return len(rows)


def drain_worker(settings: Settings, queue: str | None) -> int:
    if queue is not None:
        get_queue_policy(queue)
    worker = JobWorker(Database(settings.database_url), settings, queue_name=queue)
    return worker.drain()
