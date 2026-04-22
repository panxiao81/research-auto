from __future__ import annotations

from types import SimpleNamespace

from research_auto.interfaces.cli.app import build_parser
from research_auto.interfaces.cli import app


def test_grouped_cli_parser_exposes_new_commands() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    assert "setup" in help_text
    assert "pipeline" in help_text
    assert "inspect" in help_text
    assert "serve" in help_text
    assert "bootstrap-db" not in help_text
    assert "seed-icse" not in help_text
    assert "enqueue-resolve" not in help_text
    assert "show-paper" not in help_text


def test_grouped_cli_dispatch_names() -> None:
    parser = build_parser()

    setup_args = parser.parse_args(["setup", "bootstrap-db"])
    assert setup_args.group == "setup"
    assert setup_args.command == "bootstrap-db"

    migrate_args = parser.parse_args(["setup", "migrate"])
    assert migrate_args.group == "setup"
    assert migrate_args.command == "migrate"

    pipeline_args = parser.parse_args(["pipeline", "resolve", "--limit", "2"])
    assert pipeline_args.group == "pipeline"
    assert pipeline_args.command == "resolve"
    assert pipeline_args.limit == 2

    ask_args = parser.parse_args(["inspect", "ask", "paper", "paper-1", "Why now?"])
    assert ask_args.group == "inspect"
    assert ask_args.command == "ask"
    assert ask_args.target == "paper"
    assert ask_args.paper_id == "paper-1"
    assert ask_args.question == "Why now?"


def test_main_loads_dotenv_before_dispatch(monkeypatch) -> None:
    calls: list[str] = []

    class DummyParser:
        def parse_args(self):
            calls.append("parse_args")
            return SimpleNamespace(group="setup", command="bootstrap-db")

    monkeypatch.setattr(app, "build_parser", lambda: DummyParser())
    monkeypatch.setattr(app, "load_dotenv", lambda: calls.append("load_dotenv"))
    monkeypatch.setattr(app, "bootstrap_db", lambda: calls.append("bootstrap_db"))
    monkeypatch.setattr(app, "migrate_db", lambda: calls.append("migrate_db"))

    app.main()

    assert calls == ["load_dotenv", "parse_args", "bootstrap_db"]


def test_migrate_cli_dispatches_to_database_migration(monkeypatch) -> None:
    calls: list[str] = []

    class DummyParser:
        def parse_args(self):
            calls.append("parse_args")
            return SimpleNamespace(group="setup", command="migrate")

    monkeypatch.setattr(app, "build_parser", lambda: DummyParser())
    monkeypatch.setattr(app, "load_dotenv", lambda: calls.append("load_dotenv"))
    monkeypatch.setattr(app, "bootstrap_db", lambda: calls.append("bootstrap_db"))
    monkeypatch.setattr(app, "migrate_db", lambda: calls.append("migrate_db"))

    app.main()

    assert calls == ["load_dotenv", "parse_args", "migrate_db"]


def test_enqueue_parse_uses_checksum_aware_payload_and_dedupe(monkeypatch) -> None:
    enqueued: list[dict[str, object]] = []

    class FakeJobs:
        def list_downloaded_pdf_artifacts(self, *, limit: int | None = None):
            assert limit == 2
            return [
                {
                    "id": "artifact-1",
                    "paper_id": "paper-1",
                    "storage_uri": "local://paper-1/paper.pdf",
                    "checksum_sha256": "abc123",
                }
            ]

        def enqueue_job(self, **kwargs: object) -> None:
            enqueued.append(kwargs)

    monkeypatch.setattr(
        app,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql://example"),
    )
    monkeypatch.setattr(app, "Database", lambda url: object())
    monkeypatch.setattr(app, "PostgresJobRepository", lambda db: FakeJobs())

    app.enqueue_parse(2)

    assert enqueued == [
        {
            "job_type": "parse_artifact",
            "payload": {
                "paper_id": "paper-1",
                "artifact_id": "artifact-1",
                "storage_uri": "local://paper-1/paper.pdf",
                "checksum_sha256": "abc123",
            },
            "dedupe_key": "parse_artifact:artifact-1:abc123",
            "priority": 40,
        }
    ]
