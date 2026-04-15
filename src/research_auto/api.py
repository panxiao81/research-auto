from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from research_auto.config import get_settings
from research_auto.db import Database
from research_auto.llm import build_provider, fallback_answer_from_summary
from research_auto.web.routes import router as web_router


class QuestionRequest(BaseModel):
    question: str
    limit: int = 8


def create_app() -> FastAPI:
    settings = get_settings()
    db = Database(settings.database_url)
    provider = build_provider(settings)
    app = FastAPI(title="research-auto", version="0.1.0")
    app.state.db = db
    app.state.provider = provider
    static_dir = Path(__file__).resolve().parents[2] / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(web_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/conferences")
    def list_conferences() -> list[dict[str, object]]:
        return db.list_rows("select * from conferences order by year desc, name asc")

    @app.get("/tracks")
    def list_tracks() -> list[dict[str, object]]:
        return db.list_rows(
            """
            select t.*, c.slug as conference_slug
            from tracks t
            join conferences c on c.id = t.conference_id
            order by c.year desc, t.name asc
            """
        )

    @app.get("/papers")
    def list_papers(limit: int = 50) -> list[dict[str, object]]:
        return db.list_rows(
            """
            select p.*, c.slug as conference_slug, t.slug as track_slug
            from papers p
            join conferences c on c.id = p.conference_id
            left join tracks t on t.id = p.track_id
            order by p.created_at desc
            limit %s
            """,
            (limit,),
        )

    @app.get("/papers/{paper_id}")
    def get_paper(paper_id: str) -> dict[str, object]:
        paper = db.get_row("select * from papers where id = %s", (paper_id,))
        authors = db.list_rows(
            "select author_order, display_name, affiliation, orcid from paper_authors where paper_id = %s order by author_order asc",
            (paper_id,),
        )
        parses = db.list_rows(
            "select id, parser_version, parse_status, page_count, created_at from paper_parses where paper_id = %s order by created_at desc",
            (paper_id,),
        )
        summaries = db.list_rows(
            "select provider, model_name, prompt_version, summary_short, tags, created_at from paper_summaries where paper_id = %s order by created_at desc",
            (paper_id,),
        )
        return {"paper": paper, "authors": authors, "parses": parses, "summaries": summaries}

    @app.get("/jobs")
    def list_jobs(limit: int = 50) -> list[dict[str, object]]:
        return db.list_rows("select * from jobs order by created_at desc limit %s", (limit,))

    @app.get("/search")
    def search_papers(q: str, limit: int = 20) -> list[dict[str, object]]:
        like_q = f"%{q}%"
        return db.list_rows(
            """
            select distinct on (p.id)
                p.id,
                p.canonical_title,
                p.abstract,
                p.detail_url,
                p.best_pdf_url,
                p.status,
                s.summary_short,
                s.summary_short_zh,
                s.research_question,
                s.research_question_zh,
                ts_rank_cd(
                    setweight(to_tsvector('english', coalesce(p.canonical_title, '')), 'A') ||
                    setweight(to_tsvector('english', coalesce(p.abstract, '')), 'B') ||
                    setweight(to_tsvector('english', coalesce(s.summary_short, '')), 'B') ||
                    setweight(to_tsvector('simple', coalesce(s.summary_short_zh, '')), 'B') ||
                    setweight(to_tsvector('english', coalesce(pp.full_text, '')), 'C'),
                    plainto_tsquery('english', %s)
                ) as rank
            from papers p
            left join paper_summaries s on s.paper_id = p.id
            left join paper_parses pp on pp.paper_id = p.id
            where (
                setweight(to_tsvector('english', coalesce(p.canonical_title, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(p.abstract, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(s.summary_short, '')), 'B') ||
                setweight(to_tsvector('simple', coalesce(s.summary_short_zh, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(pp.full_text, '')), 'C')
            ) @@ plainto_tsquery('english', %s)
            or p.canonical_title ilike %s
            or coalesce(p.abstract, '') ilike %s
            or coalesce(s.summary_short, '') ilike %s
            or coalesce(s.summary_short_zh, '') ilike %s
            order by p.id, rank desc nulls last, s.created_at desc nulls last
            limit %s
            """,
            (q, q, like_q, like_q, like_q, like_q, limit),
        )

    @app.post("/ask/paper/{paper_id}")
    def ask_paper(paper_id: str, payload: QuestionRequest) -> dict[str, object]:
        rows = db.list_rows(
            """
            select content
            from paper_chunks
            where paper_id = %s
            order by ts_rank_cd(to_tsvector('english', coalesce(content, '')), plainto_tsquery('english', %s)) desc,
                     chunk_index asc
            limit %s
            """,
            (paper_id, payload.question, payload.limit),
        )
        context_chunks = [row["content"] for row in rows]
        try:
            answer = provider.answer_question(
                question=payload.question,
                paper_context="\n\n---\n\n".join(context_chunks),
                chunk_quotes=context_chunks,
            )
        except Exception:
            summary = db.get_row(
                "select * from paper_summaries where paper_id = %s order by created_at desc limit 1",
                (paper_id,),
            )
            answer = fallback_answer_from_summary(question=payload.question, summary_row=summary, chunk_quotes=context_chunks)
        return {
            "answer": answer.answer,
            "answer_zh": answer.answer_zh,
            "evidence_quotes": answer.evidence_quotes,
            "confidence": answer.confidence,
        }

    @app.post("/ask/library")
    def ask_library(payload: QuestionRequest) -> dict[str, object]:
        rows = db.list_rows(
            """
            select p.id as paper_id, p.canonical_title, pc.content
            from paper_chunks pc
            join papers p on p.id = pc.paper_id
            order by ts_rank_cd(to_tsvector('english', coalesce(pc.content, '')), plainto_tsquery('english', %s)) desc,
                     pc.created_at asc
            limit %s
            """,
            (payload.question, payload.limit),
        )
        context_chunks = [f"[{row['canonical_title']}] {row['content']}" for row in rows]
        try:
            answer = provider.answer_question(
                question=payload.question,
                paper_context="\n\n---\n\n".join(context_chunks),
                chunk_quotes=context_chunks,
            )
        except Exception:
            summary = None
            if rows:
                summary = db.get_row(
                    "select * from paper_summaries where paper_id = %s order by created_at desc limit 1",
                    (rows[0]["paper_id"],),
                )
            answer = fallback_answer_from_summary(question=payload.question, summary_row=summary, chunk_quotes=context_chunks)
        return {
            "answer": answer.answer,
            "answer_zh": answer.answer_zh,
            "evidence_quotes": answer.evidence_quotes,
            "confidence": answer.confidence,
            "papers": dedupe_papers(rows),
        }

    return app


def dedupe_papers(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for row in rows:
        paper_id = str(row["paper_id"])
        if paper_id in seen:
            continue
        seen.add(paper_id)
        result.append({"paper_id": row["paper_id"], "canonical_title": row["canonical_title"]})
    return result
