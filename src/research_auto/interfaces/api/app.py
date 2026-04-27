from __future__ import annotations

import os

from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from research_auto.infrastructure.postgres.database import Database
from research_auto.infrastructure.postgres.repositories import (
    PostgresJobRepository,
    PostgresReadRepository,
)
from research_auto.application.query_services import (
    QuestionAnswerService,
    ReadQueryService,
)
from research_auto.config import get_settings
from research_auto.infrastructure.llm.provider import build_provider
from research_auto.infrastructure.testing.fake_database import FakeDatabase
from research_auto.interfaces.web.routes import router as web_router


class QuestionRequest(BaseModel):
    question: str
    limit: int = 8


def create_app() -> FastAPI:
    settings = get_settings()
    use_fake_db = bool(os.environ.get("PYTEST_CURRENT_TEST")) or not settings.database_url
    db = FakeDatabase() if use_fake_db else Database(settings.database_url)
    provider = build_provider(settings)
    job_repository = PostgresJobRepository(db)
    read_repository = PostgresReadRepository(db)
    read_service = ReadQueryService(read_repository)
    qa_service = QuestionAnswerService(read_repository, provider)
    app = FastAPI(title="research-auto", version="0.1.0")
    app.state.db = db
    app.state.settings = settings
    app.state.provider = provider
    app.state.job_repository = job_repository
    app.state.read_service = read_service
    app.state.qa_service = qa_service
    static_dir = Path(__file__).resolve().parents[4] / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(web_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/conferences")
    def list_conferences() -> list[dict[str, object]]:
        return read_service.list_conferences()

    @app.get("/tracks")
    def list_tracks() -> list[dict[str, object]]:
        return read_service.list_tracks()

    @app.get("/papers")
    def list_papers(limit: int = 50) -> list[dict[str, object]]:
        return read_service.list_api_papers(limit=limit)

    @app.get("/papers/{paper_id}")
    def get_paper(paper_id: str) -> dict[str, object]:
        return read_service.get_api_paper(paper_id=paper_id)

    @app.get("/jobs")
    def list_jobs(limit: int = 50) -> list[dict[str, object]]:
        return read_service.list_jobs(status=None, job_type=None, limit=limit)

    @app.get("/search")
    def search_papers(q: str, limit: int = 20) -> list[dict[str, object]]:
        return read_service.search_papers(q, limit)

    @app.post("/ask/paper/{paper_id}")
    def ask_paper(paper_id: str, payload: QuestionRequest) -> dict[str, object]:
        return qa_service.ask_paper(
            paper_id=paper_id, question=payload.question, limit=payload.limit
        )

    @app.post("/ask/library")
    def ask_library(payload: QuestionRequest) -> dict[str, object]:
        return qa_service.ask_library(question=payload.question, limit=payload.limit)

    return app
