from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PaperSummary:
    problem: str
    research_question: str
    research_question_zh: str
    method: str
    evaluation: str
    results: str
    conclusions: str
    conclusions_zh: str
    future_work: str
    future_work_zh: str
    takeaway: str
    summary_short: str
    summary_long: str
    summary_short_zh: str
    summary_long_zh: str
    contributions: list[str]
    limitations: list[str]
    tags: list[str]
    raw_response: dict[str, Any]


@dataclass(slots=True)
class QuestionAnswer:
    answer: str
    answer_zh: str
    evidence_quotes: list[str]
    confidence: str
    raw_response: dict[str, Any]


def fallback_answer_from_summary(
    *, question: str, summary_row: dict[str, Any] | None, chunk_quotes: list[str]
) -> QuestionAnswer:
    if not summary_row:
        return QuestionAnswer(
            answer="I could not answer from the stored summaries, and the live model request was unavailable.",
            answer_zh="我无法从已存储的摘要中回答这个问题，而且实时模型请求当前不可用。",
            evidence_quotes=chunk_quotes[:3],
            confidence="low",
            raw_response={"fallback": "no_summary"},
        )

    q = question.lower()
    mappings = [
        (
            ["research question", "question", "研究问题"],
            "research_question",
            "research_question_zh",
        ),
        (["conclusion", "conclusions", "结论"], "conclusions", "conclusions_zh"),
        (
            ["future work", "future", "未来工作", "后续工作"],
            "future_work",
            "future_work_zh",
        ),
        (["method", "approach", "方法"], "method", None),
        (["result", "results", "finding", "结果"], "results", None),
    ]
    for keywords, en_key, zh_key in mappings:
        if any(keyword in q for keyword in keywords):
            answer = summary_row.get(en_key) or summary_row.get("summary_long") or ""
            answer_zh = (
                summary_row.get(zh_key) or summary_row.get("summary_long_zh") or ""
            )
            return QuestionAnswer(
                answer=answer,
                answer_zh=answer_zh,
                evidence_quotes=chunk_quotes[:3],
                confidence="medium",
                raw_response={"fallback": en_key},
            )

    return QuestionAnswer(
        answer=summary_row.get("summary_long")
        or summary_row.get("summary_short")
        or "",
        answer_zh=summary_row.get("summary_long_zh")
        or summary_row.get("summary_short_zh")
        or "",
        evidence_quotes=chunk_quotes[:3],
        confidence="medium",
        raw_response={"fallback": "summary_long"},
    )
