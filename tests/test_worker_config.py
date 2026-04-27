from __future__ import annotations

import logging

from research_auto.application.queue_policies import (
    get_queue_policy as get_application_queue_policy,
)
from research_auto.config import Settings
from research_auto.application.queue_policies import get_queue_policy
from research_auto.infrastructure.llm.provider import MockProvider, build_provider
from research_auto.interfaces.worker.runner import JobWorker, build_pdf_parser


def _settings(**overrides: str) -> Settings:
    values = {
        "DATABASE_URL": "postgresql://research_auto:research_auto@127.0.0.1:5432/research_auto",
        "LLM_PROVIDER": "mock",
        "LLM_MODEL": "gpt-5-mini",
    }
    values.update(overrides)
    return Settings(**values)


def test_build_provider_reuses_singleton_for_same_config() -> None:
    settings = _settings()
    provider_a = build_provider(settings)
    provider_b = build_provider(settings)

    assert provider_a is provider_b
    assert isinstance(provider_a, MockProvider)


def test_get_queue_policy_routes_llm_jobs_only() -> None:
    policy = get_queue_policy("llm")

    assert policy.name == "llm"
    assert policy.job_types == ("summarize_paper",)
    assert policy.base_retry_seconds == 30
    assert policy.rate_limit_retry_seconds == 300
    assert policy.max_running_jobs is None
    assert policy.min_start_interval_seconds == 0


def test_resolve_queue_policy_uses_slower_retries() -> None:
    policy = get_application_queue_policy("resolve")

    assert policy.job_types == ("resolve_paper_artifacts",)
    assert policy.max_running_jobs == 1
    assert policy.min_start_interval_seconds == 3
    assert policy.retry_delay_seconds(attempt_count=2, error_message="HTTP 429") == 600
    assert (
        policy.retry_delay_seconds(attempt_count=2, error_message="temporary error")
        == 120
    )


def test_get_queue_policy_rejects_unknown_queue() -> None:
    try:
        get_queue_policy("unknown")
    except ValueError as exc:
        assert "unsupported worker queue" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown queue")


def test_storage_backend_defaults_and_overrides() -> None:
    settings = _settings(STORAGE_BACKEND="s3", S3_BUCKET="papers")

    assert settings.storage_backend == "s3"
    assert settings.s3_prefix == "papers"


def test_parser_backend_defaults_to_datalab() -> None:
    settings = _settings()

    assert settings.parser_backend == "datalab"


def test_build_pdf_parser_requires_api_key_for_datalab_backend() -> None:
    try:
        build_pdf_parser(_settings(), storage=object())
    except ValueError as exc:
        assert str(exc) == (
            "DATALAB_API_KEY is required when PARSER_BACKEND=datalab"
        )
    else:
        raise AssertionError("expected ValueError when datalab backend is missing api key")


def test_build_pdf_parser_uses_pypdf_when_backend_forces_it(monkeypatch) -> None:
    settings = _settings(DATALAB_API_KEY="test-key", PARSER_BACKEND="pypdf")

    def _unexpected_datalab_parser(**kwargs: object) -> object:
        raise AssertionError(f"DatalabParser should not be constructed: {kwargs}")

    monkeypatch.setattr(
        "research_auto.interfaces.worker.runner.DatalabParser", _unexpected_datalab_parser
    )

    adapter = build_pdf_parser(settings, storage=object())

    assert adapter.datalab_parser is None


def test_build_pdf_parser_configures_datalab_sdk_parser(monkeypatch) -> None:
    settings = _settings(
        DATALAB_API_KEY="test-key",
        DATALAB_BASE_URL="https://api.example.com",
        DATALAB_TIMEOUT_SECONDS="12.5",
    )
    captured: dict[str, object] = {}

    class FakeDatalabParser:
        def __init__(self, *, api_key: str, base_url: str, timeout_seconds: float) -> None:
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            captured["timeout_seconds"] = timeout_seconds

    monkeypatch.setattr(
        "research_auto.interfaces.worker.runner.DatalabParser", FakeDatalabParser
    )

    adapter = build_pdf_parser(settings, storage=object())

    assert isinstance(adapter.datalab_parser, FakeDatalabParser)
    assert captured == {
        "api_key": "test-key",
        "base_url": "https://api.example.com",
        "timeout_seconds": 12.5,
    }


def test_build_pdf_parser_rejects_unknown_backend(monkeypatch) -> None:
    settings = _settings(PARSER_BACKEND="unknown")

    def _unexpected_datalab_parser(**kwargs: object) -> object:
        raise AssertionError(f"DatalabParser should not be constructed: {kwargs}")

    monkeypatch.setattr(
        "research_auto.interfaces.worker.runner.DatalabParser", _unexpected_datalab_parser
    )

    try:
        build_pdf_parser(settings, storage=object())
    except ValueError as exc:
        assert str(exc) == "unsupported parser backend: unknown"
    else:
        raise AssertionError("expected ValueError for unknown parser backend")


def test_build_pdf_parser_datalab_backend_still_requires_api_key(monkeypatch) -> None:
    settings = _settings(PARSER_BACKEND="datalab")

    def _unexpected_datalab_parser(**kwargs: object) -> object:
        raise AssertionError(f"DatalabParser should not be constructed: {kwargs}")

    monkeypatch.setattr(
        "research_auto.interfaces.worker.runner.DatalabParser", _unexpected_datalab_parser
    )

    try:
        build_pdf_parser(settings, storage=object())
    except ValueError as exc:
        assert str(exc) == (
            "DATALAB_API_KEY is required when PARSER_BACKEND=datalab"
        )
    else:
        raise AssertionError("expected ValueError when datalab backend is missing api key")


def test_job_worker_skips_parser_setup_for_non_parse_queue(monkeypatch) -> None:
    settings = _settings(WORKER_QUEUE="resolve")

    class FakeJobRepository:
        def __init__(self, db: object) -> None:
            self.db = db

    class FakePipelineRepository:
        def __init__(self, db: object) -> None:
            self.db = db

    monkeypatch.setattr(
        "research_auto.interfaces.worker.runner.PostgresJobRepository", FakeJobRepository
    )
    monkeypatch.setattr(
        "research_auto.interfaces.worker.runner.PostgresPipelineRepository",
        FakePipelineRepository,
    )
    monkeypatch.setattr(
        "research_auto.interfaces.worker.runner.build_storage", lambda settings: object()
    )

    def _unexpected_build_pdf_parser(*args: object, **kwargs: object) -> object:
        raise AssertionError("build_pdf_parser should not run for non-parse queues")

    monkeypatch.setattr(
        "research_auto.interfaces.worker.runner.build_pdf_parser",
        _unexpected_build_pdf_parser,
    )

    worker = JobWorker(db=object(), settings=settings)

    assert worker.queue.name == "resolve"


def test_job_worker_logs_payload_when_claiming_job(caplog) -> None:
    settings = _settings(WORKER_QUEUE="llm")

    class FakeExecutor:
        def execute(self, job: dict[str, object]) -> None:
            return None

    worker = object.__new__(JobWorker)
    worker.worker_id = "worker-1"
    worker.executor = FakeExecutor()
    worker._claim_next_job = lambda: {
        "id": "job-1",
        "job_type": "summarize_paper",
        "payload": {"paper_id": "paper-1", "paper_parse_id": "parse-1"},
    }
    worker._start_attempt = lambda job_id: "attempt-1"
    worker._succeed_job = lambda job, attempt_id: None
    worker._fail_job = lambda job, attempt_id, error_message: None

    with caplog.at_level(logging.INFO, logger="research_auto.interfaces.worker.runner"):
        processed = JobWorker.run_once(worker)

    assert processed is True
    assert any(
        'claimed job id=job-1 type=summarize_paper worker_id=worker-1 payload={"paper_id":"paper-1","paper_parse_id":"parse-1"}'
        in record.message
        for record in caplog.records
    )
