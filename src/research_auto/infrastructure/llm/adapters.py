from __future__ import annotations

from typing import Any

from research_auto.application.llm_types import PaperSummary
from research_auto.infrastructure.llm.provider import build_provider


class LiteLLMSummaryAdapter:
    def __init__(self, settings: Any) -> None:
        self.provider = build_provider(settings)
        self.provider_name = self.provider.provider_name

    def summarize(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary:
        return self.provider.summarize(title=title, abstract=abstract, chunks=chunks)
