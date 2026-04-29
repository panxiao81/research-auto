from __future__ import annotations

import os
from types import SimpleNamespace

from research_auto.config import Settings
from research_auto.infrastructure.llm.provider import (
    MockProvider,
    _provider_singletons,
    apply_env_overrides,
    build_provider,
    extract_json_from_litellm_responses,
    extract_json_from_sse_body,
    restore_env,
    safe_model_dump,
)


def _settings(**overrides: str) -> Settings:
    values = {
        "DATABASE_URL": "postgresql://research_auto:research_auto@127.0.0.1:5432/research_auto",
        "DATALAB_API_KEY": "",
        "LLM_PROVIDER": "mock",
        "LLM_MODEL": "gpt-5-mini",
    }
    values.update(overrides)
    return Settings(**values)


def test_extract_json_from_sse_body_prefers_done_event_text() -> None:
    body_text = "\n".join(
        [
            'data: {"type":"response.output_text.delta","delta":"{\\"ignored\\":false}"}',
            'data: {"type":"response.output_text.done","text":"{\\"done\\": true}"}',
            "data: [DONE]",
        ]
    )

    assert extract_json_from_sse_body(body_text) == {"done": True}


def test_extract_json_from_litellm_responses_reads_output_text_attribute() -> None:
    response = SimpleNamespace(output_text='{"source": "attribute"}')

    assert extract_json_from_litellm_responses(response) == {"source": "attribute"}


def test_extract_json_from_litellm_responses_reads_nested_output_content() -> None:
    response = {
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "output_text": '{"source": "nested"}',
                    }
                ]
            }
        ]
    }

    assert extract_json_from_litellm_responses(response) == {"source": "nested"}


def test_safe_model_dump_prefers_model_dump_method() -> None:
    class FakeResponse:
        def model_dump(self) -> dict[str, str]:
            return {"source": "model_dump"}

        def __str__(self) -> str:
            return "stringified"

    assert safe_model_dump(FakeResponse()) == {"source": "model_dump"}


def test_apply_env_overrides_and_restore_env_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("RA_EXISTING", "before")
    monkeypatch.delenv("RA_MISSING", raising=False)

    previous = apply_env_overrides({"RA_EXISTING": "after", "RA_MISSING": "created"})

    assert previous == {"RA_EXISTING": "before", "RA_MISSING": None}
    assert os.environ["RA_EXISTING"] == "after"
    assert os.environ["RA_MISSING"] == "created"

    restore_env(previous)

    assert os.environ["RA_EXISTING"] == "before"
    assert "RA_MISSING" not in os.environ


def test_build_provider_reuses_singleton_for_identical_mock_settings() -> None:
    snapshot = dict(_provider_singletons)
    _provider_singletons.clear()
    try:
        provider_a = build_provider(_settings())
        provider_b = build_provider(_settings())

        assert provider_a is provider_b
        assert isinstance(provider_a, MockProvider)
    finally:
        _provider_singletons.clear()
        _provider_singletons.update(snapshot)
