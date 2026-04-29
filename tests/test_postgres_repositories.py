from __future__ import annotations

from typing import Any

from research_auto.application.query_services import Page
from research_auto.infrastructure.postgres.repositories import PostgresReadRepository


class RecordingJobs:
    def __init__(self) -> None:
        self.fetch_one_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_all_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_one_results: list[dict[str, Any] | None] = []
        self.fetch_all_results: list[list[dict[str, Any]]] = []

    def fetch_one(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> dict[str, Any] | None:
        self.fetch_one_calls.append((query, params))
        if self.fetch_one_results:
            return self.fetch_one_results.pop(0)
        return None

    def fetch_all(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        self.fetch_all_calls.append((query, params))
        if self.fetch_all_results:
            return self.fetch_all_results.pop(0)
        return []


def make_repository(jobs: RecordingJobs) -> PostgresReadRepository:
    repository = PostgresReadRepository(db=object())
    repository.jobs = jobs
    return repository


def test_list_papers_includes_starred_filter_and_returns_page() -> None:
    jobs = RecordingJobs()
    jobs.fetch_one_results = [{"count": 1}]
    jobs.fetch_all_results = [[{"id": "paper-1", "starred": True}]]
    repository = make_repository(jobs)

    result = repository.list_papers(
        page=2,
        page_size=10,
        q=None,
        resolved=None,
        has_pdf=None,
        parsed=None,
        summarized=None,
        provider=None,
        starred=True,
        sort="updated_at",
        order="desc",
    )

    count_query, count_params = jobs.fetch_one_calls[0]
    rows_query, rows_params = jobs.fetch_all_calls[0]

    assert isinstance(result, Page)
    assert result.items == [{"id": "paper-1", "starred": True}]
    assert result.total == 1
    assert result.page == 2
    assert result.page_size == 10
    assert "p.starred = %s" in count_query
    assert count_params == (True,)
    assert "p.starred = %s" in rows_query
    assert rows_params == (True, 10, 10)


def test_set_paper_starred_passes_params_and_returns_updated_row() -> None:
    jobs = RecordingJobs()
    jobs.fetch_one_results = [{"id": "paper-1", "starred": True}]
    repository = make_repository(jobs)

    result = repository.set_paper_starred(paper_id="paper-1", starred=True)

    assert result == {"id": "paper-1", "starred": True}
    assert jobs.fetch_one_calls == [
        (
            "update papers set starred = %s where id = %s returning id, starred",
            (True, "paper-1"),
        )
    ]


def test_search_papers_includes_starred_param_only_when_present() -> None:
    jobs = RecordingJobs()
    repository = make_repository(jobs)

    repository.search_papers(q="graph", limit=5, starred=False)
    repository.search_papers(q="graph", limit=5)

    query_with_starred, params_with_starred = jobs.fetch_all_calls[0]
    query_without_starred, params_without_starred = jobs.fetch_all_calls[1]

    assert "and p.starred = %s" in query_with_starred
    assert params_with_starred == ("graph", "graph", "%graph%", "%graph%", "%graph%", False, 5)
    assert "and p.starred = %s" not in query_without_starred
    assert params_without_starred == ("graph", "graph", "%graph%", "%graph%", "%graph%", 5)


def test_list_jobs_omits_where_clause_when_filters_missing() -> None:
    jobs = RecordingJobs()
    repository = make_repository(jobs)

    repository.list_jobs(status=None, job_type=None, limit=10)

    query, params = jobs.fetch_all_calls[0]

    normalized_query = " ".join(query.split())

    assert " where " not in normalized_query
    assert "from jobs" in normalized_query
    assert "order by updated_at desc" in normalized_query
    assert normalized_query.index("from jobs") < normalized_query.index(
        "order by updated_at desc"
    )
    assert params == (10,)
