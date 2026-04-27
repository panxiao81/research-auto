from __future__ import annotations

import logging

from research_auto.domain.records import CrawlResult
from research_auto.infrastructure.crawlers.researchr import crawl_track_sync
from research_auto.infrastructure.job_logging import adapter_log_message


logger = logging.getLogger(__name__)


class ResearchrCrawlerAdapter:
    def crawl_track(self, *, track_url: str, headless: bool) -> tuple[CrawlResult, str]:
        logger.info(adapter_log_message("crawler", "start", track_url=track_url, headless=headless))
        try:
            result = crawl_track_sync(track_url, headless=headless)
        except Exception:  # noqa: BLE001
            logger.exception(
                adapter_log_message("crawler", "error", track_url=track_url, headless=headless)
            )
            raise
        logger.info(adapter_log_message("crawler", "success", track_url=track_url, headless=headless))
        return result
