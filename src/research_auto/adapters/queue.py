from __future__ import annotations

from typing import Any

from research_auto.adapters.sql import PostgresJobQueueAdmin
from research_auto.db import Database


class PostgresQueueAdapter:
    def __init__(self, db: Database) -> None:
        self.jobs = PostgresJobQueueAdmin(db)

    def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        dedupe_key: str,
        priority: int,
        max_attempts: int,
    ) -> bool:
        return self.jobs.enqueue_job(
            job_type=job_type,
            payload=payload,
            dedupe_key=dedupe_key,
            priority=priority,
            max_attempts=max_attempts,
        )
