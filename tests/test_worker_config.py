from __future__ import annotations

from research_auto.config import Settings
from research_auto.jobs import get_queue_policy
from research_auto.llm import MockProvider, build_provider


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


def test_get_queue_policy_rejects_unknown_queue() -> None:
    try:
        get_queue_policy("unknown")
    except ValueError as exc:
        assert "unsupported worker queue" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown queue")
