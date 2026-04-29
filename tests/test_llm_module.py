from __future__ import annotations

from research_auto.application.llm import build_fallback_summary


def test_build_fallback_summary_marks_raw_response_with_fallback_metadata() -> None:
    summary = build_fallback_summary(
        title="A Paper",
        abstract="Abstract",
        chunks=["chunk 1", "chunk 2"],
        error="provider timeout",
    )

    assert summary.raw_response["fallback"] is True
    assert summary.raw_response["error"] == "provider timeout"
