from __future__ import annotations

import pytest

from research_auto.application.query_services import (
    QuestionAnswerService,
    ReadQueryService,
)
from research_auto.application.llm_types import QuestionAnswer
from research_auto.interfaces.mcp.tools import (
    McpPaperLookupError,
    get_paper_tool,
    search_context_tool,
    search_papers_tool,
)


class FakeReadRepository:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, int, bool | None]] = []
        self.paper_context_calls: list[tuple[str, str, int]] = []
        self.library_context_calls: list[tuple[str, int]] = []

    def list_papers(self, **kwargs):
        return "page"

    def get_paper_detail(self, *, paper_id: str):
        return {
            "paper": {
                "canonical_title": "Hexagonal Systems",
                "conference_name": "ICSE 2026",
                "year": 2026,
                "doi": None,
                "best_landing_url": "https://example.com/paper",
            },
            "authors": [{"display_name": "Ada Lovelace"}],
            "artifacts": [],
            "parse": None,
            "chunks": [],
            "summary": None,
        }

    def search_papers(self, *, q: str, limit: int, starred: bool | None = None):
        self.search_calls.append((q, limit, starred))
        return [{"canonical_title": q, "limit": limit}]

    def get_stats(self):
        return {"papers_total": 1}

    def list_jobs(self, *, status, job_type, limit):
        return [{"status": status, "job_type": job_type, "limit": limit}]

    def list_conferences(self):
        return [{"slug": "icse-2026"}]

    def list_tracks(self):
        return [{"slug": "research-track"}]

    def list_api_papers(self, *, limit: int):
        return [{"limit": limit}]

    def get_api_paper(self, *, paper_id: str):
        return {"paper": {"id": paper_id}}

    def get_paper_question_context(self, *, paper_id: str, question: str, limit: int):
        self.paper_context_calls.append((paper_id, question, limit))
        return (["chunk 1", "chunk 2"], {"summary_long": "fallback"})

    def get_library_question_context(self, *, question: str, limit: int):
        self.library_context_calls.append((question, limit))
        return (
            ["[Paper A] chunk"],
            {"summary_long": "fallback"},
            [
                {"paper_id": "p1", "canonical_title": "Paper A"},
                {"paper_id": "p1", "canonical_title": "Paper A"},
            ],
        )


class FakeAnswerer:
    def answer_question(
        self, *, question: str, paper_context: str, chunk_quotes: list[str]
    ) -> QuestionAnswer:
        return QuestionAnswer(
            answer="ok",
            answer_zh="好",
            evidence_quotes=chunk_quotes[:1],
            confidence="high",
            raw_response={},
        )


class RaisingAnswerer:
    def answer_question(
        self, *, question: str, paper_context: str, chunk_quotes: list[str]
    ) -> QuestionAnswer:
        raise RuntimeError("llm unavailable")


def test_read_query_service_adds_bibtex() -> None:
    service = ReadQueryService(FakeReadRepository())

    detail = service.get_paper_detail("paper-1")

    assert "@inproceedings{" in detail["bibtex"]
    assert "Ada Lovelace" in detail["bibtex"]


def test_question_answer_service_dedupes_library_papers() -> None:
    service = QuestionAnswerService(FakeReadRepository(), FakeAnswerer())

    answer = service.ask_library(question="What?", limit=5)

    assert answer["answer"] == "ok"
    assert len(answer["papers"]) == 1


def test_question_answer_service_falls_back_to_summary_when_answerer_fails() -> None:
    service = QuestionAnswerService(FakeReadRepository(), RaisingAnswerer())

    answer = service.ask_paper(paper_id="paper-1", question="future work?", limit=5)

    assert answer == {
        "answer": "fallback",
        "answer_zh": "",
        "evidence_quotes": ["chunk 1", "chunk 2"],
        "confidence": "medium",
    }


def test_search_papers_tool_clamps_limit() -> None:
    repository = FakeReadRepository()
    service = ReadQueryService(repository)

    result = search_papers_tool(
        read_service=service, query="graph neural networks", limit=999
    )

    assert result["query"] == "graph neural networks"
    assert result["limit"] == 20
    assert result["results"] == [{"canonical_title": "graph neural networks", "limit": 20}]


def test_get_paper_tool_raises_for_missing_paper() -> None:
    class MissingPaperRepository(FakeReadRepository):
        def get_paper_detail(self, *, paper_id: str):
            return {
                "paper": None,
                "authors": [],
                "artifacts": [],
                "parse": None,
                "chunks": [],
                "summary": None,
            }

    service = ReadQueryService(MissingPaperRepository())

    with pytest.raises(McpPaperLookupError, match="missing-paper"):
        get_paper_tool(read_service=service, paper_id="missing-paper")


def test_search_context_tool_uses_paper_scope_when_paper_id_present() -> None:
    repository = FakeReadRepository()

    result = search_context_tool(
        repository=repository,
        query="training data",
        paper_id="paper-1",
        limit=30,
    )

    assert result == {
        "scope": "paper",
        "paper_id": "paper-1",
        "limit": 12,
        "summary": {"summary_long": "fallback"},
        "chunks": ["chunk 1", "chunk 2"],
    }


def test_search_context_tool_uses_library_scope_without_paper_id() -> None:
    repository = FakeReadRepository()

    result = search_context_tool(
        repository=repository, query="training data", paper_id=None, limit=2
    )

    assert result == {
        "scope": "library",
        "paper_id": None,
        "limit": 2,
        "summary": {"summary_long": "fallback"},
        "chunks": [
            {
                "paper_id": "p1",
                "canonical_title": "Paper A",
                "content": "[Paper A] chunk",
            }
        ],
    }
