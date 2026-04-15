from research_auto.application.llm_prompts import (
    answer_from_json,
    build_prompt,
    build_qa_prompt,
    ensure_chinese_answer_fields,
    ensure_chinese_fields,
    extract_json_from_text,
    infer_tags,
    qa_schema,
    qa_schema_text_format,
    summary_from_json,
    summary_schema,
    summary_schema_text_format,
    trim_quote,
)
from research_auto.application.llm_types import (
    PaperSummary,
    QuestionAnswer,
    fallback_answer_from_summary,
)
from research_auto.infrastructure.llm.provider import MockProvider


PROMPT_VERSION = "summary-v3"


def build_fallback_summary(
    *, title: str, abstract: str | None, chunks: list[str], error: str
) -> PaperSummary:
    base = MockProvider().summarize(title=title, abstract=abstract, chunks=chunks)
    base.raw_response = {"fallback": True, "error": error, **base.raw_response}
    return base


__all__ = [
    "PROMPT_VERSION",
    "PaperSummary",
    "QuestionAnswer",
    "answer_from_json",
    "build_fallback_summary",
    "build_prompt",
    "build_qa_prompt",
    "ensure_chinese_answer_fields",
    "ensure_chinese_fields",
    "extract_json_from_text",
    "fallback_answer_from_summary",
    "infer_tags",
    "qa_schema",
    "qa_schema_text_format",
    "summary_from_json",
    "summary_schema",
    "summary_schema_text_format",
    "trim_quote",
]
