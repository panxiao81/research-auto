from __future__ import annotations

from research_auto.application.query_services import (
    QuestionAnswerService,
    ReadQueryService,
)
from research_auto.application.llm_types import QuestionAnswer


class FakeReadRepository:
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

    def search_papers(self, *, q: str, limit: int):
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
        return (["chunk 1", "chunk 2"], {"summary_long": "fallback"})

    def get_library_question_context(self, *, question: str, limit: int):
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
