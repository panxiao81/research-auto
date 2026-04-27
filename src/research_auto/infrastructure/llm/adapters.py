from __future__ import annotations

import logging
from typing import Any

from research_auto.application.llm_types import PaperSummary
from research_auto.infrastructure.llm.provider import build_provider
from research_auto.infrastructure.job_logging import adapter_log_message


logger = logging.getLogger(__name__)


class LiteLLMSummaryAdapter:
    def __init__(self, settings: Any) -> None:
        self.provider = build_provider(settings)
        self.provider_name = self.provider.provider_name

    def summarize(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary:
        logger.info(adapter_log_message("summarizer", "start", title=title))
        try:
            summary = self.provider.summarize(title=title, abstract=abstract, chunks=chunks)
        except Exception:  # noqa: BLE001
            logger.exception(adapter_log_message("summarizer", "error", title=title))
            raise
        logger.info(
            adapter_log_message(
                "summarizer", "success", title=title, provider=self.provider_name
            )
        )
        return summary
