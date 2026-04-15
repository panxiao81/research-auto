from __future__ import annotations

from research_auto.domain.records import CrawlResult
from research_auto.infrastructure.crawlers.researchr import crawl_track_sync


class ResearchrCrawlerAdapter:
    def crawl_track(self, *, track_url: str, headless: bool) -> tuple[CrawlResult, str]:
        return crawl_track_sync(track_url, headless=headless)
