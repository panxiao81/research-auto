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

    repair_args = parser.parse_args(
        ["pipeline", "repair-running-jobs", "--older-than-seconds", "600"]
    )
    assert repair_args.group == "pipeline"
    assert repair_args.command == "repair-running-jobs"
    assert repair_args.older_than_seconds == 600

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


def test_migrate_db_prints_up_to_date_when_no_migrations_apply(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        app,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql://example"),
    )
    monkeypatch.setattr(app, "migrate_db_action", lambda settings: 0)

    app.migrate_db()

    captured = capsys.readouterr()
    assert captured.out == "database already up to date\n"


def test_repair_running_jobs_cli_dispatches_to_admin_action(monkeypatch) -> None:
    calls: list[object] = []

    class DummyParser:
        def parse_args(self):
            calls.append("parse_args")
            return SimpleNamespace(
                group="pipeline", command="repair-running-jobs", older_than_seconds=600
            )

    monkeypatch.setattr(app, "build_parser", lambda: DummyParser())
    monkeypatch.setattr(app, "load_dotenv", lambda: calls.append("load_dotenv"))
    monkeypatch.setattr(
        app,
        "repair_running_jobs",
        lambda older_than_seconds: calls.append(older_than_seconds),
    )

    app.main()

    assert calls == ["load_dotenv", "parse_args", 600]


def test_main_dispatches_inspect_ask_library(monkeypatch) -> None:
    calls: list[object] = []

    class DummyParser:
        def parse_args(self):
            calls.append("parse_args")
            return SimpleNamespace(
                group="inspect",
                command="ask",
                target="library",
                question="What changed?",
                limit=5,
            )

    monkeypatch.setattr(app, "build_parser", lambda: DummyParser())
    monkeypatch.setattr(app, "load_dotenv", lambda: calls.append("load_dotenv"))
    monkeypatch.setattr(
        app,
        "ask_library_cli",
        lambda question, limit: calls.append((question, limit)),
    )

    app.main()

    assert calls == ["load_dotenv", "parse_args", ("What changed?", 5)]


def test_enqueue_parse_cli_delegates_to_admin_action(monkeypatch) -> None:
    calls: list[object] = []

    monkeypatch.setattr(
        app,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql://example"),
    )
    monkeypatch.setattr(
        app,
        "enqueue_parse_action",
        lambda settings, limit: calls.append((settings.database_url, limit)) or 3,
    )

    app.enqueue_parse(2)

    assert calls == [("postgresql://example", 2)]
