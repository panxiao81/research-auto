from __future__ import annotations

from research_auto.domain.records import ParsedPaper
from research_auto.infrastructure.postgres.database import Database
from research_auto.infrastructure.postgres.migrations import YoyoMigrationRunner
from research_auto.infrastructure.postgres.repositories import (
    PostgresJobRepository,
    PostgresPipelineRepository,
)
from research_auto.infrastructure.postgres.schema import SCHEMA_SQL


def test_schema_adds_storage_columns_for_existing_artifacts_table() -> None:
    assert "alter table artifacts add column if not exists storage_uri text;" in SCHEMA_SQL
    assert "alter table artifacts add column if not exists storage_key text;" in SCHEMA_SQL


def test_schema_includes_parse_source_text() -> None:
    assert "source_text text not null" in SCHEMA_SQL
    assert "alter table paper_parses add column if not exists source_text text;" in SCHEMA_SQL
    assert "alter table paper_parses alter column source_text set default '';" in SCHEMA_SQL
    assert "update paper_parses set source_text = full_text where source_text is null;" in SCHEMA_SQL
    assert "alter table paper_parses alter column source_text set not null;" in SCHEMA_SQL
    assert "alter table paper_parses alter column source_text drop default;" in SCHEMA_SQL


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self._rows: list[dict[str, str]] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        self.executed.append((query, params))

    def fetchone(self) -> dict[str, str]:
        return {"id": "parse-1"}

    def fetchall(self) -> list[dict[str, str]]:
        return self._rows


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True


class _FakeDatabase:
    def __init__(self) -> None:
        self.cursor = _FakeCursor()
        self.connection = _FakeConnection(self.cursor)

    def connect(self) -> _FakeConnection:
        return self.connection


def test_yoyo_migration_runner_applies_pending_migrations_once(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeBackend:
        def lock(self):
            return self

        def __enter__(self):
            calls.append("lock")
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def to_apply(self, migrations):
            calls.append("to_apply")
            return ["migration-1"]

        def apply_migrations(self, migrations):
            calls.append(f"apply:{migrations}")

    monkeypatch.setattr(
        "research_auto.infrastructure.postgres.migrations.get_backend",
        lambda dsn: _FakeBackend(),
    )
    monkeypatch.setattr(
        "research_auto.infrastructure.postgres.migrations.read_migrations",
        lambda path: ["migration-file"],
    )

    runner = YoyoMigrationRunner("postgresql://example")
    applied = runner.migrate()

    assert applied == 1
    assert calls == ["lock", "to_apply", "apply:['migration-1']"]


def test_database_migrate_delegates_to_yoyo_runner(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeRunner:
        def __init__(self, dsn: str) -> None:
            calls.append(dsn)

        def migrate(self) -> int:
            calls.append("migrate")
            return 3

    monkeypatch.setattr(
        "research_auto.infrastructure.postgres.database.YoyoMigrationRunner",
        _FakeRunner,
    )

    database = Database("postgresql://example")
    applied = database.migrate()

    assert applied == 3
    assert calls == ["postgresql://example", "migrate"]


def test_replace_parse_persists_source_text_via_repository_behavior() -> None:
    db = _FakeDatabase()
    repository = PostgresPipelineRepository(db)  # type: ignore[arg-type]
    parsed = ParsedPaper(
        parser_version="pdf-v2",
        source_text="raw parser output",
        full_text="cleaned parser output",
        abstract_text="abstract",
        page_count=12,
        content_hash="hash-1",
        chunks=["chunk one", "chunk two"],
    )

    repository.replace_parse(
        payload={"paper_id": "paper-1", "artifact_id": "artifact-1"},
        parsed=parsed,
        prompt_version="summary-v3",
        llm_provider="github_copilot_oauth",
        llm_model="gpt-5.4-mini",
    )

    insert_query, insert_params = next(
        (query, params)
        for query, params in db.cursor.executed
        if "insert into paper_parses" in query
    )
    assert (
        "insert into paper_parses (paper_id, artifact_id, parser_version, parse_status, source_text, full_text, abstract_text, page_count, content_hash)"
        in insert_query
    )
    assert insert_params == (
        "paper-1",
        "artifact-1",
        "pdf-v2",
        "raw parser output",
        "cleaned parser output",
        "abstract",
        12,
        "hash-1",
    )
    assert db.connection.committed is True


def test_repair_running_jobs_resets_stale_jobs_and_attempts() -> None:
    db = _FakeDatabase()
    db.cursor._rows = [{"id": "job-1"}, {"id": "job-2"}]
    repository = PostgresJobRepository(db)  # type: ignore[arg-type]

    repaired = repository.repair_running_jobs(older_than_seconds=600)

    assert repaired == 2
    assert len(db.cursor.executed) == 2
    assert "update jobs" in db.cursor.executed[0][0]
    assert "status = 'pending'" in db.cursor.executed[0][0]
    assert db.cursor.executed[0][1] == ("repaired stale running job after 600 seconds", 600)
    assert db.cursor.executed[1] == (
        "update job_attempts set finished_at = now(), success = false, error_message = %s where job_id = any(%s) and finished_at is null",
        ("repaired stale running job after 600 seconds", ["job-1", "job-2"]),
    )
