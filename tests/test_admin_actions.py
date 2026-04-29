from __future__ import annotations

from types import SimpleNamespace

from research_auto.application import admin_actions


def test_enqueue_resolve_counts_only_inserted_jobs(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str], str, int]] = []

    class FakeJobRepository:
        def __init__(self, db) -> None:
            self.db = db

        def list_papers_needing_resolution(self, limit: int | None):
            assert limit == 3
            return [
                {"id": "paper-1", "detail_url": "https://example.com/1"},
                {"id": "paper-2", "detail_url": "https://example.com/2"},
                {"id": "paper-3", "detail_url": "https://example.com/3"},
            ]

        def enqueue_job(self, *, job_type: str, payload: dict[str, str], dedupe_key: str, priority: int) -> bool:
            calls.append((job_type, payload, dedupe_key, priority))
            return payload["paper_id"] != "paper-2"

    monkeypatch.setattr(admin_actions, "Database", lambda url: SimpleNamespace(url=url))
    monkeypatch.setattr(admin_actions, "PostgresJobRepository", FakeJobRepository)

    inserted = admin_actions.enqueue_resolve(
        SimpleNamespace(database_url="postgresql://example"),
        limit=3,
    )

    assert inserted == 2
    assert calls == [
        (
            "resolve_paper_artifacts",
            {"paper_id": "paper-1", "detail_url": "https://example.com/1"},
            "resolve_paper_artifacts:paper-1",
            20,
        ),
        (
            "resolve_paper_artifacts",
            {"paper_id": "paper-2", "detail_url": "https://example.com/2"},
            "resolve_paper_artifacts:paper-2",
            20,
        ),
        (
            "resolve_paper_artifacts",
            {"paper_id": "paper-3", "detail_url": "https://example.com/3"},
            "resolve_paper_artifacts:paper-3",
            20,
        ),
    ]


def test_repair_running_jobs_logs_and_returns_repaired_count(monkeypatch, caplog) -> None:
    class FakeJobRepository:
        def __init__(self, db) -> None:
            self.db = db

        def repair_running_jobs(self, *, older_than_seconds: int) -> int:
            assert older_than_seconds == 600
            return 4

    monkeypatch.setattr(admin_actions, "Database", lambda url: SimpleNamespace(url=url))
    monkeypatch.setattr(admin_actions, "PostgresJobRepository", FakeJobRepository)

    with caplog.at_level("INFO", logger="research_auto.application.admin_actions"):
        repaired = admin_actions.repair_running_jobs(
            SimpleNamespace(database_url="postgresql://example"),
            older_than_seconds=600,
        )

    assert repaired == 4
    assert any(
        record.message == "repaired 4 running jobs older than 600 seconds"
        for record in caplog.records
    )
