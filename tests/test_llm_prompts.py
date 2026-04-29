from __future__ import annotations

import subprocess
from unittest.mock import ANY

import pytest

from research_auto.application.llm_prompts import (
    answer_from_json,
    ensure_chinese_fields,
    extract_json_from_text,
    infer_tags,
    summary_from_json,
    trim_quote,
)


def test_extract_json_from_text_raises_without_json_braces() -> None:
    with pytest.raises(ValueError, match="No JSON object found"):
        extract_json_from_text("model output without json")


def test_ensure_chinese_fields_backfills_only_missing_fields(monkeypatch) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout='{"future_work_zh": "未来工作"}',
        )

    monkeypatch.setattr("research_auto.application.llm_prompts.subprocess.run", _fake_run)

    data = {
        "research_question": "What is next?",
        "research_question_zh": "已有中文",
        "conclusions": "It works.",
        "conclusions_zh": "已经有结论",
        "future_work": "Extend the benchmark.",
        "future_work_zh": "",
    }

    result = ensure_chinese_fields(data, "gpt-5.4-mini")

    assert result["research_question_zh"] == "已有中文"
    assert result["conclusions_zh"] == "已经有结论"
    assert result["future_work_zh"] == "未来工作"
    assert calls == [["codex", "exec", "--model", "gpt-5.4-mini", ANY]]


def test_trim_quote_truncates_with_ellipsis() -> None:
    assert trim_quote("x" * 710, max_chars=20) == ("x" * 17) + "..."


def test_infer_tags_collects_multiple_matches() -> None:
    assert infer_tags("LLM security testing", "dynamic bug analysis") == [
        "llm",
        "testing",
        "security",
        "analysis",
    ]


def test_summary_from_json_drops_blank_list_items() -> None:
    summary = summary_from_json(
        {
            "problem": " p ",
            "research_question": " rq ",
            "research_question_zh": " 问题 ",
            "method": " m ",
            "evaluation": " e ",
            "results": " r ",
            "conclusions": " c ",
            "conclusions_zh": " 结论 ",
            "future_work": " f ",
            "future_work_zh": " 未来 ",
            "takeaway": " t ",
            "summary_short": " short ",
            "summary_long": " long ",
            "summary_short_zh": " 短 ",
            "summary_long_zh": " 长 ",
            "contributions": [" kept ", "", "  "],
            "limitations": ["", " limitation "],
            "tags": [" llm ", " ", "analysis"],
        },
        raw_response={"provider": "test"},
    )

    assert summary.contributions == ["kept"]
    assert summary.limitations == ["limitation"]
    assert summary.tags == ["llm", "analysis"]


def test_answer_from_json_trims_long_evidence_quotes() -> None:
    answer = answer_from_json(
        {
            "answer": "Answer",
            "answer_zh": "回答",
            "evidence_quotes": ["q" * 710, "   "],
            "confidence": "high",
        },
        raw_response={"provider": "test"},
    )

    assert answer.evidence_quotes == [("q" * 697) + "..."]
