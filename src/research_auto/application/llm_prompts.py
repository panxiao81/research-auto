from __future__ import annotations

import json
import subprocess
from typing import Any

from research_auto.application.llm_types import PaperSummary, QuestionAnswer


def build_prompt(*, title: str, abstract: str | None, chunks: list[str]) -> str:
    chunk_text = "\n\n---\n\n".join(chunks[:4])
    return (
        "Read the research paper content and produce strict JSON with these keys: "
        "problem, research_question, research_question_zh, method, evaluation, results, conclusions, conclusions_zh, future_work, future_work_zh, takeaway, summary_short, summary_long, summary_short_zh, summary_long_zh, contributions, limitations, tags. "
        "Use concise technical English for the English fields and natural fluent Simplified Chinese for the Chinese fields. "
        "contributions, limitations, and tags must be arrays of strings.\n\n"
        f"Title: {title}\n\n"
        f"Abstract: {abstract or 'N/A'}\n\n"
        f"Content:\n{chunk_text}"
    )


def build_qa_prompt(
    *, question: str, paper_context: str, chunk_quotes: list[str]
) -> str:
    quotes = "\n\n".join(
        f"Quote {idx + 1}: {quote}" for idx, quote in enumerate(chunk_quotes[:8])
    )
    return (
        "Answer the user's question using only the provided paper context. "
        "Return strict JSON with keys: answer, answer_zh, evidence_quotes, confidence. "
        "Use concise technical English for answer and fluent Simplified Chinese for answer_zh. "
        "evidence_quotes must contain verbatim supporting quotes from the provided context only. "
        "If the evidence is weak, say so in the answer and use low confidence.\n\n"
        f"Question: {question}\n\n"
        f"Context:\n{paper_context}\n\n"
        f"Candidate Evidence Quotes:\n{quotes}"
    )


def summary_from_json(
    data: dict[str, Any], *, raw_response: dict[str, Any]
) -> PaperSummary:
    return PaperSummary(
        problem=str(data.get("problem") or "").strip(),
        research_question=str(data.get("research_question") or "").strip(),
        research_question_zh=str(data.get("research_question_zh") or "").strip(),
        method=str(data.get("method") or "").strip(),
        evaluation=str(data.get("evaluation") or "").strip(),
        results=str(data.get("results") or "").strip(),
        conclusions=str(data.get("conclusions") or "").strip(),
        conclusions_zh=str(data.get("conclusions_zh") or "").strip(),
        future_work=str(data.get("future_work") or "").strip(),
        future_work_zh=str(data.get("future_work_zh") or "").strip(),
        takeaway=str(data.get("takeaway") or "").strip(),
        summary_short=str(data.get("summary_short") or "").strip(),
        summary_long=str(data.get("summary_long") or "").strip(),
        summary_short_zh=str(data.get("summary_short_zh") or "").strip(),
        summary_long_zh=str(data.get("summary_long_zh") or "").strip(),
        contributions=[
            str(item).strip()
            for item in data.get("contributions", [])
            if str(item).strip()
        ],
        limitations=[
            str(item).strip()
            for item in data.get("limitations", [])
            if str(item).strip()
        ],
        tags=[str(item).strip() for item in data.get("tags", []) if str(item).strip()],
        raw_response=raw_response,
    )


def answer_from_json(
    data: dict[str, Any], *, raw_response: dict[str, Any]
) -> QuestionAnswer:
    return QuestionAnswer(
        answer=str(data.get("answer") or "").strip(),
        answer_zh=str(data.get("answer_zh") or "").strip(),
        evidence_quotes=[
            trim_quote(str(item).strip())
            for item in data.get("evidence_quotes", [])
            if str(item).strip()
        ],
        confidence=str(data.get("confidence") or "").strip(),
        raw_response=raw_response,
    )


def summary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "problem": {"type": "string"},
            "research_question": {"type": "string"},
            "research_question_zh": {"type": "string"},
            "method": {"type": "string"},
            "evaluation": {"type": "string"},
            "results": {"type": "string"},
            "conclusions": {"type": "string"},
            "conclusions_zh": {"type": "string"},
            "future_work": {"type": "string"},
            "future_work_zh": {"type": "string"},
            "takeaway": {"type": "string"},
            "summary_short": {"type": "string"},
            "summary_long": {"type": "string"},
            "summary_short_zh": {"type": "string"},
            "summary_long_zh": {"type": "string"},
            "contributions": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "problem",
            "research_question",
            "research_question_zh",
            "method",
            "evaluation",
            "results",
            "conclusions",
            "conclusions_zh",
            "future_work",
            "future_work_zh",
            "takeaway",
            "summary_short",
            "summary_long",
            "summary_short_zh",
            "summary_long_zh",
            "contributions",
            "limitations",
            "tags",
        ],
        "additionalProperties": False,
    }


def summary_schema_text_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "paper_summary",
        "strict": True,
        "schema": summary_schema(),
    }


def qa_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "answer_zh": {"type": "string"},
            "evidence_quotes": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string"},
        },
        "required": ["answer", "answer_zh", "evidence_quotes", "confidence"],
        "additionalProperties": False,
    }


def qa_schema_text_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "paper_answer",
        "strict": True,
        "schema": qa_schema(),
    }


def infer_tags(title: str, text: str) -> list[str]:
    lowered = f"{title} {text}".lower()
    tags: list[str] = []
    for tag, needles in {
        "llm": ["llm", "large language model", "genai"],
        "testing": ["test", "fuzz", "bug"],
        "security": ["security", "vulnerability", "secret", "privacy"],
        "analysis": ["analysis", "static", "dynamic", "verification"],
    }.items():
        if any(needle in lowered for needle in needles):
            tags.append(tag)
    return tags


def extract_json_from_text(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in Codex CLI output")
    return json.loads(text[start : end + 1])


def ensure_chinese_fields(data: dict[str, Any], model: str) -> dict[str, Any]:
    required_pairs = [
        ("research_question", "research_question_zh"),
        ("conclusions", "conclusions_zh"),
        ("future_work", "future_work_zh"),
    ]
    missing_pairs = [
        (en_key, zh_key)
        for en_key, zh_key in required_pairs
        if data.get(en_key) and not data.get(zh_key)
    ]
    missing = [zh_key for _, zh_key in missing_pairs]
    if not missing:
        return data
    payload = {zh_key: data.get(en_key, "") for en_key, zh_key in missing_pairs}
    prompt = (
        "Translate the following research-summary fields into concise, natural Simplified Chinese. "
        "Return strict JSON only with the same *_zh keys.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    completed = subprocess.run(
        ["codex", "exec", "--model", model, prompt],
        check=True,
        capture_output=True,
        text=True,
    )
    translated = extract_json_from_text(completed.stdout)
    for key in missing:
        data[key] = translated.get(key, "")
    return data


def ensure_chinese_answer_fields(data: dict[str, Any], model: str) -> dict[str, Any]:
    if data.get("answer") and not data.get("answer_zh"):
        prompt = (
            "Translate the following answer into concise, natural Simplified Chinese. Return strict JSON only with key answer_zh.\n\n"
            + json.dumps({"answer_zh": data["answer"]}, ensure_ascii=False)
        )
        completed = subprocess.run(
            ["codex", "exec", "--model", model, prompt],
            check=True,
            capture_output=True,
            text=True,
        )
        translated = extract_json_from_text(completed.stdout)
        data["answer_zh"] = translated.get("answer_zh", "")
    return data


def trim_quote(text: str, max_chars: int = 700) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."
