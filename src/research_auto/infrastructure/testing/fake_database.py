from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


def _normalize_sql(query: str) -> str:
    return " ".join(query.lower().split())


@dataclass(frozen=True, slots=True)
class _FixtureSummary:
    provider: str = "github_copilot_oauth"
    model_name: str = "gpt-5.4-mini"
    prompt_version: str = "summary-v3"


class FakeDatabase:
    def __init__(self) -> None:
        self.dsn = "memory://research-auto"
        self._paper = {
            "id": "a7ccafea-b80f-4a01-bc18-42347badee49",
            "conference_id": "conference-1",
            "conference_name": "ICSE 2026",
            "conference_slug": "icse-2026",
            "track_id": "track-1",
            "track_name": "Research Track",
            "track_slug": "research-track",
            "source_paper_key": "single-tester-limits",
            "canonical_title": "Breaking Single-Tester Limits",
            "title_normalized": "breaking single tester limits",
            "abstract": "We explore how teams can move beyond single-tester limitations.",
            "year": 2026,
            "paper_type": "research",
            "session_name": "Session 1",
            "detail_url": "https://example.com/papers/breaking-single-tester-limits",
            "canonical_url": "https://example.com/papers/breaking-single-tester-limits",
            "best_pdf_url": "/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/artifacts/artifact-1",
            "best_landing_url": "https://example.com/papers/breaking-single-tester-limits",
            "doi": None,
            "arxiv_id": None,
            "openreview_id": None,
            "source_confidence": 0.990,
            "starred": False,
            "resolution_status": "resolved",
            "status": "discovered",
            "created_at": "2026-04-27 12:00:00+00:00",
            "updated_at": "2026-04-27 12:00:00+00:00",
        }
        self._authors = [
            {"author_order": 1, "display_name": "Ada Lovelace", "affiliation": None, "orcid": None},
            {"author_order": 2, "display_name": "Grace Hopper", "affiliation": None, "orcid": None},
        ]
        self._artifact = {
            "artifact_kind": "publisher_pdf",
            "label": "Publisher PDF",
            "resolution_reason": "direct_pdf",
            "resolved_url": self._paper["best_pdf_url"],
            "download_status": "downloaded",
            "local_path": None,
        }
        self._parse = {
            "id": "parse-1",
            "paper_id": self._paper["id"],
            "artifact_id": "artifact-1",
            "parser_version": "pdf-v2",
            "parse_status": "succeeded",
            "source_text": "raw extracted text",
            "full_text": "clean extracted text",
            "abstract_text": "This paper explores testing at scale.",
            "page_count": 12,
            "content_hash": "hash-1",
            "created_at": "2026-04-27 12:00:00+00:00",
            "updated_at": "2026-04-27 12:00:00+00:00",
        }
        self._chunks = [
            {
                "chunk_index": 0,
                "token_count": 120,
                "content": "This paper explores how to break the single-tester bottleneck.",
            },
            {
                "chunk_index": 1,
                "token_count": 96,
                "content": "The evaluation shows improved throughput for collaborative testing.",
            },
        ]
        self._summary = {
            "id": "summary-1",
            "paper_id": self._paper["id"],
            "paper_parse_id": self._parse["id"],
            "provider": _FixtureSummary.provider,
            "model_name": _FixtureSummary.model_name,
            "prompt_version": _FixtureSummary.prompt_version,
            "problem": "Single-tester workflows are too slow.",
            "research_question": "How can testing be scaled beyond a single tester?",
            "research_question_zh": "如何突破单测试者限制？",
            "method": "A workflow redesign with shared artifacts.",
            "evaluation": "A controlled evaluation on team throughput.",
            "results": "The new workflow improves throughput.",
            "conclusions": "Collaborative testing can reduce bottlenecks.",
            "conclusions_zh": "协作式测试可以减少瓶颈。",
            "future_work": "Study larger teams and more domains.",
            "future_work_zh": "未来工作：研究更大的团队和更多领域。",
            "takeaway": "Testing throughput matters.",
            "summary_short": "A paper about removing single-tester limits.",
            "summary_long": "This paper studies a collaborative workflow for scaling testing.",
            "summary_short_zh": "这篇论文讨论如何突破单测试者限制。",
            "summary_long_zh": "这篇论文介绍了一种用于扩展测试协作的工作流。",
            "contributions": ["Shared artifacts", "Workflow redesign"],
            "limitations": ["Small evaluation"],
            "tags": ["testing", "workflow"],
            "raw_response": {"provider": _FixtureSummary.provider},
            "created_at": "2026-04-27 12:00:00+00:00",
            "updated_at": "2026-04-27 12:00:00+00:00",
        }
        self._job = {
            "id": "job-1",
            "job_type": "crawl_track",
            "status": "succeeded",
            "priority": 10,
            "attempt_count": 1,
            "max_attempts": 5,
            "last_error": None,
            "updated_at": "2026-04-27 12:00:00+00:00",
        }

    @contextmanager
    def connect(self) -> Iterator[_FakeConnection]:
        yield _FakeConnection(self)

    def migrate(self) -> int:
        return 0

    def bootstrap(self) -> None:
        return None

    def _paper_rows(self) -> list[dict[str, Any]]:
        paper = dict(self._paper)
        paper.update(
            {
                "track_name": self._paper["track_name"],
                "page_count": self._parse["page_count"],
                "latest_parse_id": self._parse["id"],
                "latest_summary_id": self._summary["id"],
                "summary_short": self._summary["summary_short"],
                "summary_short_zh": self._summary["summary_short_zh"],
                "research_question_zh": self._summary["research_question_zh"],
                "provider": self._summary["provider"],
                "model_name": self._summary["model_name"],
                "prompt_version": self._summary["prompt_version"],
                "starred": self._paper["starred"],
                "has_pdf": True,
                "is_parsed": True,
                "is_summarized": True,
                "has_chinese_summary": True,
                "is_fallback_summary": False,
            }
        )
        return [paper]

    def _matches_paper_filters(self, query: str, params: tuple[Any, ...]) -> bool:
        if "ilike %s" in query:
            search = str(params[0]).strip("%").lower()
            haystack = " ".join(
                [
                    self._paper["canonical_title"],
                    self._paper["abstract"] or "",
                    self._summary["summary_short"] or "",
                    self._summary["summary_short_zh"] or "",
                ]
            ).lower()
            if search and search not in haystack:
                return False

        index = 0
        if "(p.resolution_status = 'resolved') = %s" in query:
            if bool(params[index]) is False:
                return False
            index += 1
        if "(p.best_pdf_url is not null) = %s" in query:
            if bool(params[index]) is False:
                return False
            index += 1
        if "(lp.id is not null) = %s" in query:
            if bool(params[index]) is False:
                return False
            index += 1
        if "(ls.id is not null) = %s" in query:
            if bool(params[index]) is False:
                return False
            index += 1
        if "coalesce(ls.provider, '') = %s" in query:
            if str(params[index]) != self._summary["provider"]:
                return False
            index += 1
        if "p.starred = %s" in query:
            if bool(params[index]) != bool(self._paper["starred"]):
                return False
        return True

    def query(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        normalized = _normalize_sql(query)

        if normalized == "select count(*) as count from papers":
            return [{"count": 1}]
        if normalized == "select count(*) as count from papers where resolution_status = 'resolved'":
            return [{"count": 1}]
        if normalized == "select count(*) as count from papers where resolution_status = 'unresolved'":
            return [{"count": 0}]
        if normalized == "select count(*) as count from papers where best_pdf_url is not null":
            return [{"count": 1}]
        if normalized == "select count(*) as count from papers where resolution_status = 'resolved' and best_pdf_url is null":
            return [{"count": 0}]
        if normalized == "select count(*) as count from paper_parses":
            return [{"count": 1}]
        if normalized == "select count(*) as count from paper_summaries":
            return [{"count": 1}]
        if normalized == "select count(*) as count from jobs where status = 'failed'":
            return [{"count": 0}]

        if normalized == "select provider, count(*) as count from paper_summaries group by provider order by count desc":
            return [{"provider": self._summary["provider"], "count": 1}]
        if normalized == "select artifact_kind, count(*) as count from artifacts group by artifact_kind order by count desc":
            return [{"artifact_kind": self._artifact["artifact_kind"], "count": 1}]
        if normalized == "select job_type, count(*) as count from jobs where status = 'failed' group by job_type order by count desc":
            return []
        if normalized == "select coalesce(a.resolution_reason, 'no_reason_recorded') as reason, count(*) as count from artifacts a where a.artifact_kind = 'fallback_to_arxiv' group by coalesce(a.resolution_reason, 'no_reason_recorded') order by count desc":
            return []

        if normalized == "select distinct provider from paper_summaries where provider is not null order by provider asc":
            return [{"provider": self._summary["provider"]}]

        if normalized == "update papers set starred = %s where id = %s returning id, starred":
            starred, paper_id = params
            if paper_id != self._paper["id"]:
                return []
            self._paper["starred"] = bool(starred)
            return [{"id": self._paper["id"], "starred": self._paper["starred"]}]

        if normalized.startswith("select p.id, p.canonical_title, p.year, p.session_name, p.best_pdf_url, p.resolution_status, p.updated_at,"):
            if not self._matches_paper_filters(query.lower(), params):
                return []
            return self._paper_rows()

        if normalized.startswith("select count(*) as count from papers p join conferences c on c.id = p.conference_id"):
            return [{"count": 1 if self._matches_paper_filters(query.lower(), params) else 0}]

        if normalized.startswith("select p.*, c.name as conference_name, c.slug as conference_slug, t.name as track_name"):
            return [dict(self._paper)]
        if normalized == "select author_order, display_name, affiliation from paper_authors where paper_id = %s order by author_order asc":
            return [dict(row) for row in self._authors]
        if normalized == "select artifact_kind, label, resolution_reason, resolved_url, download_status, local_path from artifacts where paper_id = %s order by created_at asc":
            return [dict(self._artifact)]
        if normalized == "select * from paper_parses where paper_id = %s order by created_at desc limit 1":
            return [dict(self._parse)]
        if normalized == "select chunk_index, token_count, left(content, 1200) as content from paper_chunks where paper_id = %s order by chunk_index asc limit 12":
            return [dict(row) for row in self._chunks]
        if normalized == "select * from paper_summaries where paper_id = %s order by created_at desc limit 1":
            return [dict(self._summary)]

        if normalized.startswith("select distinct on (p.id)"):
            if len(params) == 7:
                starred_filter = bool(params[5])
                if starred_filter != bool(self._paper["starred"]):
                    return []
            return [
                {
                    "id": self._paper["id"],
                    "canonical_title": self._paper["canonical_title"],
                    "best_pdf_url": self._paper["best_pdf_url"],
                    "resolution_status": self._paper["resolution_status"],
                    "starred": self._paper["starred"],
                    "summary_short": self._summary["summary_short"],
                    "summary_short_zh": self._summary["summary_short_zh"],
                    "research_question": self._summary["research_question"],
                    "research_question_zh": self._summary["research_question_zh"],
                    "rank": 1.0,
                }
            ]

        if normalized == "select * from conferences order by year desc, name asc":
            return [
                {
                    "id": self._paper["conference_id"],
                    "slug": self._paper["conference_slug"],
                    "name": self._paper["conference_name"],
                    "year": self._paper["year"],
                    "homepage_url": "https://example.com/conference",
                    "source_system": "fixture",
                    "created_at": self._paper["created_at"],
                    "updated_at": self._paper["updated_at"],
                }
            ]
        if normalized == "select t.*, c.slug as conference_slug from tracks t join conferences c on c.id = t.conference_id order by c.year desc, t.name asc":
            return [
                {
                    "id": self._paper["track_id"],
                    "conference_id": self._paper["conference_id"],
                    "slug": self._paper["track_slug"],
                    "name": self._paper["track_name"],
                    "track_url": "https://example.com/track",
                    "conference_slug": self._paper["conference_slug"],
                }
            ]
        if normalized == "select p.*, c.slug as conference_slug, t.slug as track_slug from papers p join conferences c on c.id = p.conference_id left join tracks t on t.id = p.track_id order by p.created_at desc limit %s":
            row = dict(self._paper)
            row["conference_slug"] = self._paper["conference_slug"]
            row["track_slug"] = self._paper["track_slug"]
            return [row]
        if normalized == "select * from papers where id = %s":
            return [dict(self._paper)]
        if normalized == "select author_order, display_name, affiliation, orcid from paper_authors where paper_id = %s order by author_order asc":
            return [dict(row) for row in self._authors]
        if normalized == "select id, parser_version, parse_status, page_count, created_at from paper_parses where paper_id = %s order by created_at desc":
            return [
                {
                    "id": self._parse["id"],
                    "parser_version": self._parse["parser_version"],
                    "parse_status": self._parse["parse_status"],
                    "page_count": self._parse["page_count"],
                    "created_at": self._parse["created_at"],
                }
            ]
        if normalized == "select provider, model_name, prompt_version, summary_short, tags, created_at from paper_summaries where paper_id = %s order by created_at desc":
            return [
                {
                    "provider": self._summary["provider"],
                    "model_name": self._summary["model_name"],
                    "prompt_version": self._summary["prompt_version"],
                    "summary_short": self._summary["summary_short"],
                    "tags": self._summary["tags"],
                    "created_at": self._summary["created_at"],
                }
            ]
        if normalized == "select id, job_type, status, priority, attempt_count, max_attempts, last_error, updated_at from jobs  order by updated_at desc limit %s":
            return [dict(self._job)]
        if normalized == "select id, job_type, status, priority, attempt_count, max_attempts, last_error, updated_at from jobs where status = %s order by updated_at desc limit %s":
            return [dict(self._job)] if params and params[0] == self._job["status"] else []

        if normalized == "select * from papers order by created_at desc limit %s":
            return [dict(self._paper)]

        if normalized == "select p.canonical_title, p.doi, p.detail_url, p.best_pdf_url, exists(select 1 from artifacts a where a.paper_id = p.id and a.artifact_kind = 'manual_pdf' and a.storage_uri is not null) as has_manual_pdf, exists(select 1 from paper_parses pp where pp.paper_id = p.id) as has_parse, exists(select 1 from paper_summaries ps where ps.paper_id = p.id) as has_summary from papers p where p.id = %s":
            return [
                {
                    "canonical_title": self._paper["canonical_title"],
                    "doi": self._paper["doi"],
                    "detail_url": self._paper["detail_url"],
                    "best_pdf_url": self._paper["best_pdf_url"],
                    "has_manual_pdf": False,
                    "has_parse": True,
                    "has_summary": True,
                }
            ]

        return []


class _FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._database)

    def commit(self) -> None:
        return None

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeCursor:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self._rows = self._database.query(query, params)
        self.rowcount = len(self._rows)

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None
