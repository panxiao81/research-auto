from __future__ import annotations

import json
from typing import Any

from research_auto.db import Database


class PostgresSqlQueries:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_rows(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return list(cur.fetchall())

    def get_row(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchone()


class PostgresCatalogAdmin:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_conference(
        self,
        *,
        slug: str,
        name: str,
        year: int,
        homepage_url: str,
        source_system: str,
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
        self,
        *,
        conference_id: str,
        slug: str,
        name: str,
        track_url: str,
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


class PostgresJobQueueAdmin:
    def __init__(self, db: Database) -> None:
        self.db = db

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
