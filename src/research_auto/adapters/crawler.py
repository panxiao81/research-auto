from __future__ import annotations

from research_auto.crawlers.researchr import crawl_track_sync
from research_auto.models import CrawlResult


class ResearchrCrawlerAdapter:
    def crawl_track(self, *, track_url: str, headless: bool) -> tuple[CrawlResult, str]:
        return crawl_track_sync(track_url, headless=headless)
